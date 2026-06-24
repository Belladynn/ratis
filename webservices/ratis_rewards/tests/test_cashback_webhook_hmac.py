"""TDD — POST /rewards/cashback/webhook/{provider}

F-RW-6 (deep audit RW 2026-05-10) — bearer-token auth replaced by HMAC-SHA256
signature with Stripe-style header ``X-Cashback-Signature: t=<ts>,v1=<sig>``.

AUDIT 2026-05-17 (M-finding) — the single shared ``CASHBACK_WEBHOOK_SECRET``
was replaced by **per-provider** secrets ``CASHBACK_WEBHOOK_SECRET_{PROVIDER}``
(uppercased provider name). A leaked secret now compromises a single
provider, not every affiliate network. The handler selects the secret for
the identified provider and verifies against ONLY that one.

Coverage :
  * Valid signature with provider secret  → 200
  * Provider X presented with provider Y's secret → 401 (cross-provider
    rejection — the security property of the per-provider split)
  * Valid signature with provider PREV secret → 200 (overlap rotation)
  * Tampered body / wrong sig             → 401 invalid_signature
  * Stale timestamp (> tolerance)         → 401 signature_expired
  * Future timestamp (> tolerance)        → 401 signature_expired
  * Missing X-Cashback-Signature header   → 401 missing_signature
  * Malformed header (no t / no v1)       → 401 invalid_signature
  * Unknown provider                      → 401 unknown_provider
  * Bearer token (legacy path)            → 401 missing_signature

The HTTP layer is exercised end-to-end via :class:`fastapi.testclient
.TestClient` ; the underlying ``resolve_cashback`` service is monkey-patched
to a no-op so the test only asserts auth behaviour. A separate test verifies
that an authenticated call DOES reach the service (success-path wiring).
"""

from __future__ import annotations

import hmac
import json
import time
import uuid
from collections.abc import Callable
from hashlib import sha256

import pytest
from routes.rewards import cashback_webhook as wh_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Per-provider secrets — mirror the values set in ``conftest.py``. Each
#: provider has its own distinct secret so a leak is contained to one
#: affiliate network.
PROVIDER_SECRETS = {
    "affilae": "test-webhook-secret-affilae",
    "awin": "test-webhook-secret-awin",
    "cj": "test-webhook-secret-cj",
}


def _sign(body: bytes, secret: str, ts: int) -> str:
    """Compute the v1 hex signature the route will check against."""
    signed_payload = f"{ts}.".encode("ascii") + body
    return hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()


def _make_payload() -> bytes:
    return json.dumps({"transaction_id": str(uuid.uuid4()), "resolution": "confirmed"}).encode("utf-8")


@pytest.fixture
def stub_resolve(monkeypatch):
    """Replace ``resolve_cashback`` by a no-op so we only assert auth.

    The patched callable still mutates db (it issues a harmless SELECT 1)
    so the route's ``db_transaction`` context manager records a real write
    cycle — keeps the assert_no_pending_changes fixture honest without
    needing a real cashback row.
    """
    calls: list[tuple] = []

    def fake_resolve(db, tx_id, resolution, rewards_cfg):
        # Touch the session so the surrounding db_transaction context
        # manager has something to commit/rollback. SELECT 1 is a safe
        # no-write probe.
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        calls.append((tx_id, resolution))

    monkeypatch.setattr(wh_mod, "resolve_cashback", fake_resolve)
    return calls


@pytest.fixture
def signing_helpers() -> dict[str, Callable[..., dict]]:
    """Return helpers building ``(body_bytes, headers)`` pairs."""

    def with_sig(secret: str, *, ts: int | None = None) -> dict:
        body = _make_payload()
        ts = int(time.time()) if ts is None else ts
        sig = _sign(body, secret, ts)
        return {
            "body": body,
            "headers": {
                "X-Cashback-Signature": f"t={ts},v1={sig}",
                "Content-Type": "application/json",
            },
        }

    def with_raw_header(raw: str) -> dict:
        body = _make_payload()
        return {
            "body": body,
            "headers": {
                "X-Cashback-Signature": raw,
                "Content-Type": "application/json",
            },
        }

    return {"with_sig": with_sig, "with_raw_header": with_raw_header}


# ---------------------------------------------------------------------------
# Happy path — each provider verified with its own secret
# ---------------------------------------------------------------------------


def test_valid_signature_with_provider_secret_returns_200(client, stub_resolve, signing_helpers):
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"])
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert len(stub_resolve) == 1


def test_each_provider_accepted_with_its_own_secret(client, stub_resolve, signing_helpers):
    """Every allowlisted provider authenticates with its OWN secret."""
    for provider, secret in PROVIDER_SECRETS.items():
        pkt = signing_helpers["with_sig"](secret)
        resp = client.post(
            f"/api/v1/rewards/cashback/webhook/{provider}",
            content=pkt["body"],
            headers=pkt["headers"],
        )
        assert resp.status_code == 200, (provider, resp.text)


# ---------------------------------------------------------------------------
# Cross-provider rejection — THE security property of this fix
# ---------------------------------------------------------------------------


def test_provider_x_rejects_provider_y_secret(client, stub_resolve, signing_helpers):
    """Core security property : a webhook for provider X signed with
    provider Y's secret MUST be rejected. With per-provider secrets a leak
    of Y's secret cannot be used to forge X's webhooks."""
    # Sign with awin's secret, send to affilae's endpoint.
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["awin"])
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"
    assert stub_resolve == []


def test_cross_provider_rejection_all_pairs(client, stub_resolve, signing_helpers):
    """Exhaustive : for every ordered pair (X, Y) with X != Y, a webhook
    for X signed with Y's secret is rejected."""
    for target in PROVIDER_SECRETS:
        for other, other_secret in PROVIDER_SECRETS.items():
            if other == target:
                continue
            pkt = signing_helpers["with_sig"](other_secret)
            resp = client.post(
                f"/api/v1/rewards/cashback/webhook/{target}",
                content=pkt["body"],
                headers=pkt["headers"],
            )
            assert resp.status_code == 401, (target, other, resp.text)
            assert resp.json()["detail"] == "invalid_signature"
    assert stub_resolve == []


# ---------------------------------------------------------------------------
# Overlap rotation — per-provider PREV secret
# ---------------------------------------------------------------------------


def test_valid_signature_with_provider_prev_secret_returns_200(client, stub_resolve, signing_helpers, monkeypatch):
    """Overlap-rotation : a request signed with the *previous* secret of a
    provider must still be accepted while that provider's
    ``CASHBACK_WEBHOOK_SECRET_<PROVIDER>_PREV`` is set."""
    prev = "old-rotation-secret-affilae"
    monkeypatch.setenv("CASHBACK_WEBHOOK_SECRET_AFFILAE_PREV", prev)
    pkt = signing_helpers["with_sig"](prev)
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert len(stub_resolve) == 1


def test_prev_secret_is_per_provider(client, stub_resolve, signing_helpers, monkeypatch):
    """A PREV secret set for provider X does NOT authenticate provider Y —
    the rotation window is itself per-provider."""
    prev = "old-rotation-secret-affilae"
    monkeypatch.setenv("CASHBACK_WEBHOOK_SECRET_AFFILAE_PREV", prev)
    # Sign awin's webhook with affilae's PREV secret → must fail.
    pkt = signing_helpers["with_sig"](prev)
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/awin",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"
    assert stub_resolve == []


def test_prev_secret_unused_when_env_unset(client, stub_resolve, signing_helpers, monkeypatch):
    """A signature made with an arbitrary secret is rejected when no PREV
    secret is configured for that provider."""
    monkeypatch.setenv("CASHBACK_WEBHOOK_SECRET_AFFILAE_PREV", "")
    pkt = signing_helpers["with_sig"]("some-other-key")
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"
    assert stub_resolve == []


# ---------------------------------------------------------------------------
# Tampering / replay / clock-skew
# ---------------------------------------------------------------------------


def test_tampered_body_rejected(client, stub_resolve, signing_helpers):
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"])
    tampered = pkt["body"].replace(b"confirmed", b"refused")
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=tampered,
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"
    assert stub_resolve == []


def test_stale_timestamp_rejected(client, stub_resolve, signing_helpers):
    """A signature with ``t`` older than the tolerance (default 300 s) is
    rejected to thwart replay of leaked-and-stale captures."""
    stale_ts = int(time.time()) - 3600  # 1h old
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"], ts=stale_ts)
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "signature_expired"
    assert stub_resolve == []


def test_far_future_timestamp_rejected(client, stub_resolve, signing_helpers):
    """A timestamp far in the future is also rejected — symmetrical
    tolerance closes the clock-rewind attack."""
    future_ts = int(time.time()) + 3600
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"], ts=future_ts)
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "signature_expired"


def test_just_within_tolerance_accepted(client, stub_resolve, signing_helpers):
    """A 299 s old signature stays within the 300 s window."""
    near_ts = int(time.time()) - 299
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"], ts=near_ts)
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def test_missing_signature_header(client, stub_resolve):
    body = _make_payload()
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_signature"


def test_malformed_signature_header_no_v1(client, stub_resolve, signing_helpers):
    ts = int(time.time())
    pkt = signing_helpers["with_raw_header"](f"t={ts}")
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"


def test_malformed_signature_header_no_t(client, stub_resolve, signing_helpers):
    pkt = signing_helpers["with_raw_header"]("v1=deadbeef")
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"


def test_malformed_signature_header_non_int_timestamp(client, stub_resolve, signing_helpers):
    pkt = signing_helpers["with_raw_header"]("t=notanumber,v1=deadbeef")
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"


def test_legacy_bearer_token_rejected(client, stub_resolve):
    """The previous auth was ``Authorization: Bearer <secret>`` — verify
    that a request shaped this way (no signature header) is now refused.
    Guards against accidental partner regression."""
    body = _make_payload()
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=body,
        headers={
            "Authorization": f"Bearer {PROVIDER_SECRETS['affilae']}",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_signature"


# ---------------------------------------------------------------------------
# Provider allowlist
# ---------------------------------------------------------------------------


def test_unknown_provider_rejected_with_valid_signature(client, stub_resolve, signing_helpers):
    """Even a perfectly-signed request must be refused when the path
    parameter is not in the allowlist — limits blast radius if a secret
    leaks AND an attacker tries to flood unknown provider names."""
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"])
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/UNKNOWN_PARTNER",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "unknown_provider"
    assert stub_resolve == []


def test_provider_without_configured_secret_rejected(client, stub_resolve, signing_helpers, monkeypatch):
    """An allowlisted provider whose secret env var is unset/empty must be
    refused — a mis-configured deploy must never silently accept all
    signatures for that provider."""
    monkeypatch.setenv("CASHBACK_WEBHOOK_SECRET_CJ", "")
    monkeypatch.setenv("CASHBACK_WEBHOOK_SECRET_CJ_PREV", "")
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["cj"])
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/cj",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"
    assert stub_resolve == []


# ---------------------------------------------------------------------------
# Sanity — auth success still reaches the resolver
# ---------------------------------------------------------------------------


def test_authenticated_call_invokes_resolver(client, stub_resolve, signing_helpers):
    """Confirms the wiring : a fully-valid request actually dispatches to
    ``resolve_cashback`` with the parsed payload."""
    pkt = signing_helpers["with_sig"](PROVIDER_SECRETS["affilae"])
    resp = client.post(
        "/api/v1/rewards/cashback/webhook/affilae",
        content=pkt["body"],
        headers=pkt["headers"],
    )
    assert resp.status_code == 200
    parsed = json.loads(pkt["body"])
    assert stub_resolve == [(uuid.UUID(parsed["transaction_id"]), "confirmed")]
