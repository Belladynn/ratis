"""Stripe HTTP API v2024 wrappers — Module 5 of agent-mcp (ARCH § Module 5).

Exposes 4 typed tools to Claude Code agents :

* `stripe_list_customers`        (ops)   — GET  /v1/customers
* `stripe_get_subscription`      (ops)   — GET  /v1/subscriptions/<id>
* `stripe_list_recent_charges`   (ops)   — GET  /v1/charges
* `stripe_refund_charge`         (admin) — POST /v1/refunds   (form-encoded)

Backend choice (per ARCH § Module 5)
------------------------------------
We hit `api.stripe.com/v1/` directly via `httpx` rather than depending on the
`stripe` Python SDK. HTTP-direct keeps the test surface identical to the other
modules (`glitchtip_tools` / `github_tools`) and avoids pulling
the SDK + its sub-deps into our virtualenv (DA-49 — typed Python wrappers,
not third-party clients).

Form-encoded POST bodies (Stripe-specific)
------------------------------------------
Stripe's API uses ``application/x-www-form-urlencoded`` for POST bodies, NOT
JSON. The `stripe_refund_charge` tool sends the body via httpx's `data=` kwarg
which triggers form encoding automatically. Forgetting this is the #1
Stripe-binding bug — there's an explicit test asserting the content-type.

Token discipline (security-critical, DA-43)
-------------------------------------------
* Token is fetched FRESH from `Keychain` on every call (account name
  ``stripe``). The keychain itself has a 60-second positive cache.
* Token only lives in the `Authorization: Bearer <token>` header of the
  outbound httpx request. It NEVER appears in tool args, URLs, request
  bodies, returned dicts, exceptions, audit log entries or stderr. The
  cross-tool sweep test asserts this exhaustively.
* `KeychainMiss` propagates verbatim so the dispatcher tags the audit
  status `keychain_miss` and the operator knows to run
  `agent-mcp keychain set stripe <token>`.

Live-mode warning (V0 = test mode, V1 = live)
---------------------------------------------
Per ARCH § Module 5 : V0 (pre-Runa-KYB) runs in test mode (`sk_test_...`),
V1 (post-KYB) runs in live mode (`sk_live_...`). When a `sk_live_` key is
detected, the wrapper writes a one-shot `live_mode_used` warning to the
audit log on first call within the process. This is **non-blocking** — live
mode is a legit V1 state, the warning just makes it visible to operators
tailing the log so accidental V0 → V1 leakage is loud.

The warning is one-shot per process to avoid log spam ; subsequent calls
within the same process are silent. The token prefix itself is **never**
written to the log — only the boolean fact that a live key was used.

Scopes (DA-44)
--------------
Stripe touches money. Read tools are `ops`-scoped (operators can debug
cashback/Stripe flows from their MCP role). The single mutating tool
(`stripe_refund_charge`) is `admin`-scoped — refunds are irreversible and
must be issued only with the admin token. Auth gate enforces this BEFORE
the tool body runs (so an ops caller can't even spawn the form-encoded POST).

References
----------
* ARCH_agent_mcp.md § Module 5 (signatures + scopes)
* DA-43 (Keychain), DA-44 (scopes), DA-48 (audit), DA-49 (typed Python tools)
* Stripe REST API : https://stripe.com/docs/api
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from ..audit import AuditLog
from ..config import audit_log_file
from ..errors import AgentMcpError, ProviderError
from ..keychain import Keychain
from ..server import TOOLS_REGISTRY, register_tool

STRIPE_BASE_URL = "https://api.stripe.com"
"""Stripe HTTP API base URL — no trailing slash, paths start with `/v1/...`."""

KEYCHAIN_ACCOUNT = "stripe"
"""Account name in the macOS Keychain under service `ratis-agent-mcp`."""

HTTP_TIMEOUT_SEC = 30.0
"""Per-request timeout for outbound Stripe calls.

Stripe is generally fast (<1s) but transient slowness on /charges (large
account histories) can push us toward 5-10s. 30s is generous ; we do NOT
retry — the dispatcher surfaces the failure to the agent which can decide
to retry contextually.
"""

STRIPE_LIMIT_MAX = 100
"""Stripe's API caps `limit` at 100 across all paginated list endpoints."""

LIVE_KEY_PREFIX = "sk_live_"
"""Prefix that identifies a live-mode Stripe secret key."""


# ---- internal helpers ---------------------------------------------------


def _fetch_token() -> str:
    """Read the Stripe secret key from the macOS Keychain.

    A fresh `Keychain()` is constructed each call — same pattern as
    `glitchtip_tools` / `github_tools`. Tests monkeypatch
    `Keychain.get`.

    Raises `KeychainMiss` if the entry is missing — propagated as-is so the
    dispatcher tags the audit line `keychain_miss`.
    """
    return Keychain().get(KEYCHAIN_ACCOUNT)


def _build_client(token: str) -> httpx.Client:
    """Construct the per-call `httpx.Client` carrying the Bearer token.

    Tests monkeypatch this function to inject an `httpx.MockTransport`. The
    real implementation never needs an explicit transport — httpx defaults
    are fine for our scale.

    Note the `Content-Type` is NOT set at the client level — Stripe's POSTs
    use form encoding (`data=` kwarg) and httpx auto-fills the right header
    per request. Setting it client-wide would break GETs (which have no body).
    """
    return httpx.Client(
        base_url=STRIPE_BASE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=HTTP_TIMEOUT_SEC,
    )


def _raise_for_status(response: httpx.Response, *, context: str) -> None:
    """Convert a non-2xx response into a `ProviderError` with a stable shape.

    We deliberately surface the upstream status + a truncated body — never
    the request headers (where the Bearer token lives).
    """
    if 200 <= response.status_code < 300:
        return
    body_preview = response.text[:500] if response.text else ""
    raise ProviderError(f"stripe {context} failed: HTTP {response.status_code} — {body_preview}".rstrip(" —"))


def _clamp_limit(limit: int) -> int:
    """Clamp `limit` to Stripe's [1, 100] range.

    Defensive : agents may pass nonsensical values ; the API would 400 on
    them anyway, but we'd rather burn a token than a round-trip.
    """
    if limit < 1:
        return 1
    if limit > STRIPE_LIMIT_MAX:
        return STRIPE_LIMIT_MAX
    return limit


# ---- live-mode warning -------------------------------------------------

# Module-level memo of "we already warned about live mode in this process".
# A simple bool is enough — we don't track per-key because there's only ever
# one Stripe key in the keychain at a time.
_LIVE_MODE_WARNED: bool = False

# Test-only injection point. Production code calls `_default_audit()` which
# resolves the audit log path lazily (so a missing state dir doesn't crash
# at import time). Tests inject a known instance via `_set_audit_log_for_tests`.
_AUDIT_OVERRIDE: AuditLog | None = None


def _set_audit_log_for_tests(audit: AuditLog) -> None:
    """Test-only — point live-mode warnings at a specific AuditLog.

    Production code never calls this. Tests use it so the warning lands in
    the same `tmp_path/audit.log` the dispatcher uses, allowing assertions
    on the merged log.
    """
    global _AUDIT_OVERRIDE
    _AUDIT_OVERRIDE = audit


def _reset_live_mode_warned() -> None:
    """Test-only — clear the one-shot memo so each test starts fresh."""
    global _LIVE_MODE_WARNED
    _LIVE_MODE_WARNED = False


def _default_audit() -> AuditLog:
    """Resolve the production audit log lazily — one fresh writer per call.

    Cheap (no file I/O happens until `.write()`). We do NOT cache an instance
    because the global state-dir might be redirected between calls (e.g.
    test fixture sets `XDG_STATE_HOME` after import).
    """
    return AuditLog(audit_log_file())


def _warn_if_live_mode(token: str) -> None:
    """Write a one-shot `live_mode_used` audit line if `token` is live-mode.

    Idempotent within a process : the second call is a silent no-op even if
    the live token is still in use. Token VALUE is never written to the log
    — only the fact that a live key was detected.

    Audit failures are swallowed (the dispatcher will still write its own
    line for the actual tool call) — we don't want the warning subsystem to
    block the tool call.
    """
    global _LIVE_MODE_WARNED
    if _LIVE_MODE_WARNED:
        return
    if not token.startswith(LIVE_KEY_PREFIX):
        return

    audit = _AUDIT_OVERRIDE or _default_audit()
    with contextlib.suppress(AgentMcpError, OSError):
        audit.write(
            caller="stripe_tools",
            tool="stripe",
            args_redacted={"warning": "sk_live_ key detected — V1 mode in use"},
            status="live_mode_used",
            latency_ms=0,
            error=None,
        )
    _LIVE_MODE_WARNED = True


# ---- tool implementations -----------------------------------------------


def stripe_list_customers(limit: int = 10, email: str | None = None) -> list[dict[str, Any]]:
    """List Stripe customers (filtered by email if provided). Read-only. Scope: ops.

    Args :
        limit : max customers returned (Stripe caps at 100 ; we clamp defensively).
        email : if non-None, scopes the list to customers whose email matches.

    Returns the raw `data` list under Stripe's response envelope — each entry
    is a dict with at least ``id``, ``email``, ``created``, ``metadata``.
    """
    token = _fetch_token()
    _warn_if_live_mode(token)

    params: dict[str, str] = {"limit": str(_clamp_limit(limit))}
    if email is not None:
        params["email"] = email

    with _build_client(token) as client:
        response = client.get("/v1/customers", params=params)
    _raise_for_status(response, context="list_customers")
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ProviderError(f"stripe list_customers: unexpected payload shape {type(payload).__name__}")
    return payload["data"]


def stripe_get_subscription(subscription_id: str) -> dict[str, Any]:
    """Get subscription details. Read-only. Scope: ops.

    Args :
        subscription_id : the Stripe subscription id (e.g. ``sub_123``).

    Returns the full subscription dict including ``status``,
    ``current_period_end``, ``items``, ``metadata``.
    """
    token = _fetch_token()
    _warn_if_live_mode(token)

    with _build_client(token) as client:
        response = client.get(f"/v1/subscriptions/{subscription_id}")
    _raise_for_status(response, context="get_subscription")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError(f"stripe get_subscription: expected dict, got {type(payload).__name__}")
    return payload


def stripe_list_recent_charges(limit: int = 20) -> list[dict[str, Any]]:
    """List most recent charges (debugging cashback flow). Read-only. Scope: ops.

    Args :
        limit : max charges returned (Stripe caps at 100 ; we clamp defensively).

    Returns the raw `data` list — each entry is a dict with at least ``id``,
    ``amount``, ``status``, ``customer``, ``created``.
    """
    token = _fetch_token()
    _warn_if_live_mode(token)

    with _build_client(token) as client:
        response = client.get(
            "/v1/charges",
            params={"limit": str(_clamp_limit(limit))},
        )
    _raise_for_status(response, context="list_recent_charges")
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ProviderError(f"stripe list_recent_charges: unexpected payload shape {type(payload).__name__}")
    return payload["data"]


def stripe_refund_charge(
    charge_id: str,
    amount_cents: int | None = None,
    reason: str = "requested_by_customer",
) -> dict[str, Any]:
    """Issue a refund. Mutating, irreversible side-effect. Scope: admin.

    Stripe expects the body as ``application/x-www-form-urlencoded``, NOT
    JSON — we pass `data=` to httpx which sets the right Content-Type and
    encodes the dict as form fields automatically.

    Args :
        charge_id    : the Stripe charge id to refund (e.g. ``ch_abc``).
        amount_cents : optional partial-refund amount in cents. When `None`,
                       Stripe issues a full refund (omitting `amount` from the
                       form body — Stripe infers from the charge).
        reason       : Stripe's enumerated reason code. Default is
                       ``"requested_by_customer"`` ; other valid values are
                       ``"duplicate"`` and ``"fraudulent"``.

    Returns the created refund dict (with ``id``, ``status``, ``amount``,
    ``charge``, etc.).
    """
    token = _fetch_token()
    _warn_if_live_mode(token)

    form: dict[str, str] = {"charge": charge_id, "reason": reason}
    if amount_cents is not None:
        form["amount"] = str(amount_cents)

    with _build_client(token) as client:
        response = client.post("/v1/refunds", data=form)
    _raise_for_status(response, context="refund_charge")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError(f"stripe refund_charge: expected dict, got {type(payload).__name__}")
    return payload


# ---- registration -------------------------------------------------------

# Imperative registration — mirrors `glitchtip_tools` / `github_tools` /
# `eas_tools`. The autouse `reset_tools_registry` test
# fixture clears the registry, so we re-populate deterministically.

_REGISTERED = False


def register_all() -> None:
    """Register the 4 Stripe tools into the module-level registry.

    Per ARCH § Module 5 :
    * 3 read-only tools → ops scope (operators can debug billing flows).
    * 1 mutating tool (`stripe_refund_charge`) → admin scope (irreversible
      money movement, gated at the auth layer before invocation).

    Idempotent — subsequent calls are no-ops, so importing this module from
    multiple places (CLI bootstrap, tests, future docs generators) is safe.
    """
    global _REGISTERED
    if _REGISTERED and "stripe_list_customers" in TOOLS_REGISTRY:
        return

    if "stripe_list_customers" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(stripe_list_customers)
    if "stripe_get_subscription" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(stripe_get_subscription)
    if "stripe_list_recent_charges" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(stripe_list_recent_charges)
    if "stripe_refund_charge" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(stripe_refund_charge)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs."""
    global _REGISTERED
    _REGISTERED = False
