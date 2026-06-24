"""
Webhook endpoint for partner cashback status notifications.

POST /rewards/cashback/webhook/{provider}

Authentication
--------------
Stripe-style HMAC signature over ``"{timestamp}.{raw_body}"`` with
SHA-256, sent in the request header :

    X-Cashback-Signature: t=<unix_ts>,v1=<hex_signature>

The handler :

1. Rejects unknown ``provider`` path parameters using the allowlist from
   ``ratis_settings.json § cashback.webhook_providers``.
2. Parses the signature header — missing / malformed → 401.
3. Rejects signatures whose timestamp is outside ±``cashback
   .webhook_timestamp_tolerance_seconds`` (default 300 s) → blocks replays.
4. Computes the expected HMAC with the **per-provider** secret
   ``CASHBACK_WEBHOOK_SECRET_{PROVIDER}`` (uppercased provider name) ;
   falls back to ``CASHBACK_WEBHOOK_SECRET_{PROVIDER}_PREV`` if set
   (overlap-rotation window, see ARCH_cashback.md § Webhook auth).
   A PREV-match logs a warning so the rotation can be observed.

   Per-provider secrets (AUDIT 2026-05-17) replaced the former single
   shared ``CASHBACK_WEBHOOK_SECRET``: with one shared secret a leak let
   an attacker forge webhooks for *every* affiliate network. Each
   provider now has its own secret, so a leak is contained to one
   provider — the handler verifies against ONLY the identified
   provider's secret.
5. Uses :func:`hmac.compare_digest` to thwart timing side-channels.

Body schema (V1, generic — to be adapted per partner) ::

    { "transaction_id": "<uuid>", "resolution": "confirmed" | "refused" }

Bearer-token authentication was replaced in F-RW-6 (deep audit RW
2026-05-10) because a leaked token allowed unbounded replay. HMAC + a 5
min timestamp window confines the exposure of a leaked secret to that
window.
"""

from __future__ import annotations

import hmac
import logging
import os
import uuid
from hashlib import sha256

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ratis_core.database import get_db
from ratis_core.exceptions import Conflict, NotFound
from services.cashback_service import resolve_cashback
from sqlalchemy.orm import Session

router = APIRouter()
log = logging.getLogger(__name__)

#: Header carrying the Stripe-style ``t=<ts>,v1=<sig>`` payload.
SIGNATURE_HEADER = "X-Cashback-Signature"

#: Default tolerance when ``ratis_settings.json`` does not surface the
#: ``webhook_timestamp_tolerance_seconds`` key — keeps boot-time
#: configuration optional while preserving a safe default.
DEFAULT_TIMESTAMP_TOLERANCE_SECONDS = 300


def _parse_signature_header(raw: str) -> tuple[int, str]:
    """Parse ``t=<unix_ts>,v1=<hex>``. Raises :class:`ValueError` on any
    malformation (missing key, non-int timestamp, empty signature)."""
    parts: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        parts[k.strip()] = v.strip()
    if "t" not in parts or "v1" not in parts:
        raise ValueError("missing t / v1")
    if not parts["v1"]:
        raise ValueError("empty v1")
    ts = int(parts["t"])  # ValueError if not int
    return ts, parts["v1"]


def _expected_signature(secret: str, ts: int, raw_body: bytes) -> str:
    """Compute HMAC-SHA256 over ``"{ts}.{body}"`` and return hex digest."""
    signed_payload = f"{ts}.".encode("ascii") + raw_body
    return hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()


def _provider_secret_env(provider: str) -> str:
    """Env var name holding the current HMAC secret for ``provider``.

    Per-provider secrets (AUDIT 2026-05-17) — derived dynamically because
    the provider set is config-driven (``cashback.webhook_providers``).
    The provider has already been validated against the allowlist before
    this is called, so ``provider`` is a known, safe identifier.
    """
    return f"CASHBACK_WEBHOOK_SECRET_{provider.upper()}"


def _now_unix() -> int:
    """Wall-clock seconds since the epoch — extracted for monkeypatching."""
    import time

    return int(time.time())


class WebhookPayload(BaseModel):
    transaction_id: uuid.UUID
    resolution: str  # "confirmed" | "refused"


@router.post(
    "/rewards/cashback/webhook/{provider}",
    status_code=200,
)
async def cashback_webhook(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Partner webhook — notifies of cashback validation or refusal.

    Authenticated by HMAC-SHA256 signature (see module docstring).
    """
    cfg = request.app.state.cfg
    cashback_cfg = cfg.get("cashback", {})
    allowed_providers: list[str] = cashback_cfg.get("webhook_providers", [])
    tolerance = int(
        cashback_cfg.get(
            "webhook_timestamp_tolerance_seconds",
            DEFAULT_TIMESTAMP_TOLERANCE_SECONDS,
        )
    )

    # 1. Provider allowlist — fail fast before reading the body so an
    #    attacker probing arbitrary providers learns nothing about our
    #    signature scheme.
    if provider not in allowed_providers:
        raise HTTPException(status_code=401, detail="unknown_provider")

    # 2. Read the raw body once — needed for HMAC, then re-parsed as JSON.
    raw_body = await request.body()

    # 3. Parse signature header.
    sig_header = request.headers.get(SIGNATURE_HEADER, "")
    if not sig_header:
        raise HTTPException(status_code=401, detail="missing_signature")
    try:
        ts, provided_sig = _parse_signature_header(sig_header)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid_signature")

    # 4. Timestamp window — reject replays / clock-skew far outside ±tolerance.
    now = _now_unix()
    if abs(now - ts) > tolerance:
        raise HTTPException(status_code=401, detail="signature_expired")

    # 5. HMAC verify against the IDENTIFIED PROVIDER's secret then its
    #    optional PREV secret (overlap-rotation window). Per-provider
    #    secrets (AUDIT 2026-05-17) — a leaked secret only forges webhooks
    #    for that one provider, never the whole affiliate network set.
    secret_env = _provider_secret_env(provider)
    current_secret = os.environ.get(secret_env, "")
    prev_secret = os.environ.get(f"{secret_env}_PREV", "")
    if not current_secret:
        # Defensive — main.py lifespan already enforces this at boot, but a
        # mis-configured deploy must not silently accept all signatures.
        raise HTTPException(status_code=401, detail="invalid_signature")

    matched = False
    if hmac.compare_digest(provided_sig, _expected_signature(current_secret, ts, raw_body)):
        matched = True
    elif prev_secret and hmac.compare_digest(provided_sig, _expected_signature(prev_secret, ts, raw_body)):
        matched = True
        log.warning(
            "cashback_webhook: signature verified with PREV secret — overlap rotation in progress (provider=%s)",
            provider,
        )

    if not matched:
        raise HTTPException(status_code=401, detail="invalid_signature")

    # 6. Parse + dispatch payload only AFTER auth — never leak Pydantic
    #    validation errors before the signature is cleared.
    try:
        body = WebhookPayload.model_validate_json(raw_body)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_body")

    if body.resolution not in ("confirmed", "refused"):
        raise HTTPException(status_code=422, detail="invalid_resolution")

    rewards_cfg = cfg["rewards"]
    try:
        with db_transaction(db):
            resolve_cashback(db, body.transaction_id, body.resolution, rewards_cfg)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail)
    return {"ok": True}
