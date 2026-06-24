"""TDD coverage for `agent_mcp.tools.stripe_tools`.

Strategy
--------
* The 4 Stripe tools are pure Python functions wrapping `httpx.Client` calls
  against `api.stripe.com/v1/`.
* We inject an `httpx.MockTransport` so no real network is touched and we can
  assert exactly what HTTP request the tool issued (URL, method, headers, body).
* `Keychain.get` is monkeypatched to return a fake `sk_test_...` token — we
  force test mode for the bulk of the suite to avoid any live-mode noise, and
  exercise live-mode detection in dedicated tests.
* Audit assertions go through the `Dispatcher` so we cover the full
  registration + dispatch + audit pipeline (the same code path Claude will
  exercise at runtime).

Form-encoded bodies (Stripe-specific)
-------------------------------------
Stripe's REST API takes POST bodies as ``application/x-www-form-urlencoded``,
NOT JSON. The `stripe_refund_charge` tool MUST send the form encoding ; we
assert this explicitly because every Stripe-binding bug in the wild starts
with someone forgetting it.

Live-mode warning (V0 = test mode only)
---------------------------------------
Per ARCH § Module 5, V0 = test mode (sk_test_...), V1 (post-Runa-KYB) = live
mode. The wrapper detects `sk_live_` keys and writes a one-shot audit entry
flagging the live-mode use — non-blocking, but visible to operators tailing
the log. Tests :
* `sk_test_xxx` → no warning entry written (silence).
* `sk_live_xxx` → exactly one extra audit line with status `live_mode_used`
  on first call within the process.

Token-leak guard (security-critical)
------------------------------------
Several tests assert :
* the fake token never appears in the audit JSONL ;
* the substring ``Bearer`` (i.e. the Authorization header) never leaks ;
* the cross-tool sweep walks every captured request to verify the fake token
  is only in headers, never in URLs / bodies / arg values.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from agent_mcp import keychain as keychain_mod
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.errors import KeychainMiss, ProviderError
from agent_mcp.server import Dispatcher
from agent_mcp.tools import stripe_tools

FAKE_TEST_TOKEN = "sk_test_unit_DO_NOT_LEAK"  # pragma: allowlist secret
FAKE_LIVE_TOKEN = "sk_live_unit_DO_NOT_LEAK"  # pragma: allowlist secret


# -- shared fixtures ------------------------------------------------------


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Patch `Keychain.get` so the Stripe tools see a fake test-mode token."""

    def _fake_get(self: keychain_mod.Keychain, account: str) -> str:
        assert account == "stripe", f"unexpected keychain account {account!r}"
        return FAKE_TEST_TOKEN

    monkeypatch.setattr(keychain_mod.Keychain, "get", _fake_get)
    # Reset the live-mode "already-warned" memo so each test has a fresh state.
    stripe_tools._reset_live_mode_warned()
    return FAKE_TEST_TOKEN


@pytest.fixture
def live_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Patch `Keychain.get` to return a live (`sk_live_`) token."""

    def _fake_get(self: keychain_mod.Keychain, account: str) -> str:
        assert account == "stripe"
        return FAKE_LIVE_TOKEN

    monkeypatch.setattr(keychain_mod.Keychain, "get", _fake_get)
    stripe_tools._reset_live_mode_warned()
    return FAKE_LIVE_TOKEN


@pytest.fixture
def captured_requests() -> list[httpx.Request]:
    """List populated by the mock transport with every outbound request."""
    return []


@pytest.fixture
def install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    captured_requests: list[httpx.Request],
) -> Iterator[dict[str, Any]]:
    """Replace `stripe_tools._build_client` so it returns a client wired to a
    `httpx.MockTransport`. Tests pre-set `responder` to control the response.

    Returns the dict the test mutates : `{"responder": <callable>}`.
    """
    state: dict[str, Any] = {
        "responder": lambda req: httpx.Response(200, json={}),  # default OK
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return state["responder"](request)

    transport = httpx.MockTransport(_handler)

    real_build = stripe_tools._build_client

    def fake_build_client(token: str) -> httpx.Client:
        return httpx.Client(
            base_url=stripe_tools.STRIPE_BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            transport=transport,
            timeout=stripe_tools.HTTP_TIMEOUT_SEC,
        )

    monkeypatch.setattr(stripe_tools, "_build_client", fake_build_client)
    yield state
    stripe_tools._build_client = real_build  # type: ignore[assignment]


@pytest.fixture
def dispatcher(tmp_path: Path) -> Dispatcher:
    """Dispatcher backed by a temp audit log + admin/ops tokens."""
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    stripe_tools.register_all()
    # Wire the audit log into stripe_tools so live-mode warnings can write
    # to the same log the dispatcher uses (test-only injection).
    stripe_tools._set_audit_log_for_tests(audit)
    return Dispatcher(auth=auth, audit=audit)


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# -- happy paths ----------------------------------------------------------


def test_list_customers_happy_path(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`stripe_list_customers` GETs /v1/customers and returns the data list."""
    fake_response = {
        "object": "list",
        "data": [
            {"id": "cus_1", "email": "a@b.com"},
            {"id": "cus_2", "email": "c@d.com"},
        ],
        "has_more": False,
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = stripe_tools.stripe_list_customers(limit=2)

    assert result == fake_response["data"]
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/customers"
    assert req.url.params["limit"] == "2"
    assert "email" not in req.url.params  # not provided
    assert req.headers["Authorization"] == f"Bearer {fake_token}"


def test_list_customers_with_email_filter(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """When `email` is provided, it goes into the URL query string."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers(limit=5, email="someone@example.com")

    req = captured_requests[0]
    assert req.url.params["email"] == "someone@example.com"
    assert req.url.params["limit"] == "5"


def test_list_customers_default_limit(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Default limit is 10 (per ARCH § Module 5)."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers()

    assert captured_requests[0].url.params["limit"] == "10"


def test_list_customers_clamps_limit_to_100(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Stripe API caps `limit` at 100 — tool clamps defensively."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers(limit=500)

    assert captured_requests[0].url.params["limit"] == "100"


def test_list_customers_clamps_limit_to_1(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Limit floor at 1 (Stripe rejects 0 / negatives)."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers(limit=0)

    assert captured_requests[0].url.params["limit"] == "1"


def test_get_subscription_happy_path(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`stripe_get_subscription` GETs /v1/subscriptions/<id>."""
    fake_response = {
        "id": "sub_123",
        "object": "subscription",
        "status": "active",
        "current_period_end": 1735689600,
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = stripe_tools.stripe_get_subscription(subscription_id="sub_123")

    assert result == fake_response
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/subscriptions/sub_123"


def test_list_recent_charges_happy_path(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`stripe_list_recent_charges` GETs /v1/charges and returns the data list."""
    fake_response = {
        "object": "list",
        "data": [
            {"id": "ch_1", "amount": 1000, "status": "succeeded"},
            {"id": "ch_2", "amount": 2000, "status": "succeeded"},
        ],
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = stripe_tools.stripe_list_recent_charges(limit=2)

    assert result == fake_response["data"]
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/charges"
    assert req.url.params["limit"] == "2"


def test_list_recent_charges_default_limit(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Default limit is 20 (per ARCH § Module 5)."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_recent_charges()

    assert captured_requests[0].url.params["limit"] == "20"


def test_list_recent_charges_clamps_limit(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Limit clamped to [1, 100]."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_recent_charges(limit=500)
    stripe_tools.stripe_list_recent_charges(limit=-5)

    assert captured_requests[0].url.params["limit"] == "100"
    assert captured_requests[1].url.params["limit"] == "1"


def test_refund_charge_happy_path_form_encoded(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`stripe_refund_charge` POSTs /v1/refunds with form-encoded body (NOT JSON)."""
    fake_response = {
        "id": "re_123",
        "object": "refund",
        "amount": 1000,
        "charge": "ch_abc",
        "status": "succeeded",
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = stripe_tools.stripe_refund_charge(charge_id="ch_abc", amount_cents=1000)

    assert result == fake_response
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert req.url.path == "/v1/refunds"
    # Critical : form-encoded, NOT JSON.
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    body = req.content.decode("utf-8")
    parsed = parse_qs(body)
    assert parsed == {
        "charge": ["ch_abc"],
        "amount": ["1000"],
        "reason": ["requested_by_customer"],
    }


def test_refund_charge_without_amount_full_refund(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """When `amount_cents=None`, the field is omitted (Stripe treats omission as full refund)."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"id": "re_x"})

    stripe_tools.stripe_refund_charge(charge_id="ch_full")

    body = captured_requests[0].content.decode("utf-8")
    parsed = parse_qs(body)
    assert "amount" not in parsed  # full refund — Stripe infers from charge
    assert parsed["charge"] == ["ch_full"]
    assert parsed["reason"] == ["requested_by_customer"]


def test_refund_charge_custom_reason(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Custom `reason` flows into the form body."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"id": "re_x"})

    stripe_tools.stripe_refund_charge(charge_id="ch_dup", reason="duplicate")

    body = captured_requests[0].content.decode("utf-8")
    parsed = parse_qs(body)
    assert parsed["reason"] == ["duplicate"]


# -- error paths ----------------------------------------------------------


def test_missing_token_raises_keychain_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the keychain entry is absent, the tool surfaces `KeychainMiss`."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="stripe"):
        stripe_tools.stripe_list_customers()


def test_provider_4xx_raises_provider_error(
    fake_token: str,
    install_mock_transport: dict[str, Any],
) -> None:
    """Stripe returning 4xx is wrapped in `ProviderError`."""
    install_mock_transport["responder"] = lambda req: httpx.Response(
        404, json={"error": {"message": "no such customer"}}
    )

    with pytest.raises(ProviderError, match="404"):
        stripe_tools.stripe_get_subscription(subscription_id="sub_nope")


def test_provider_5xx_raises_provider_error(
    fake_token: str,
    install_mock_transport: dict[str, Any],
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(503, text="upstream down")

    with pytest.raises(ProviderError, match="503"):
        stripe_tools.stripe_list_customers()


# -- live-mode warning ---------------------------------------------------


def test_test_mode_silent_no_warning(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `sk_test_xxx` key triggers NO live-mode warning in the audit log."""
    audit = AuditLog(tmp_path / "audit.log")
    stripe_tools._set_audit_log_for_tests(audit)
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers()

    # No `live_mode_used` line should appear.
    if (tmp_path / "audit.log").exists():
        lines = _audit_lines(tmp_path / "audit.log")
        live_lines = [ln for ln in lines if ln.get("status") == "live_mode_used"]
        assert live_lines == [], f"unexpected live-mode warning: {live_lines}"


def test_live_mode_writes_warning_audit_line(
    live_token: str,
    install_mock_transport: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A `sk_live_xxx` key triggers exactly ONE warning line in the audit log."""
    audit = AuditLog(tmp_path / "audit.log")
    stripe_tools._set_audit_log_for_tests(audit)
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers()

    lines = _audit_lines(tmp_path / "audit.log")
    live_lines = [ln for ln in lines if ln.get("status") == "live_mode_used"]
    assert len(live_lines) == 1
    assert live_lines[0]["tool"] == "stripe"
    # Token MUST NOT leak even into the warning entry.
    raw = (tmp_path / "audit.log").read_text()
    assert FAKE_LIVE_TOKEN not in raw
    assert "sk_live_unit" not in raw  # no token substring


def test_live_mode_warning_is_one_shot(
    live_token: str,
    install_mock_transport: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Multiple calls within the same process → still only ONE warning line."""
    audit = AuditLog(tmp_path / "audit.log")
    stripe_tools._set_audit_log_for_tests(audit)
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": []})

    stripe_tools.stripe_list_customers()
    stripe_tools.stripe_list_customers()
    stripe_tools.stripe_list_customers()

    lines = _audit_lines(tmp_path / "audit.log")
    live_lines = [ln for ln in lines if ln.get("status") == "live_mode_used"]
    assert len(live_lines) == 1


# -- registration & dispatch (full pipeline) ------------------------------


@pytest.mark.asyncio
async def test_dispatch_list_customers_audits_ok(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": [{"id": "cus_1"}]})

    outcome = await dispatcher.dispatch(
        tool_name="stripe_list_customers",
        arguments={"limit": 1},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == [{"id": "cus_1"}]

    lines = _audit_lines(tmp_path / "audit.log")
    # Expect exactly one ok line for stripe_list_customers (no live warning — test mode).
    tool_lines = [ln for ln in lines if ln["tool"] == "stripe_list_customers"]
    assert len(tool_lines) == 1
    assert tool_lines[0]["status"] == "ok"
    assert tool_lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_get_subscription_audits_ok(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"id": "sub_x"})

    outcome = await dispatcher.dispatch(
        tool_name="stripe_get_subscription",
        arguments={"subscription_id": "sub_x"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"

    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "stripe_get_subscription"]
    assert tool_lines[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_list_recent_charges_audits_ok(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": [{"id": "ch_1"}]})

    outcome = await dispatcher.dispatch(
        tool_name="stripe_list_recent_charges",
        arguments={"limit": 1},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"

    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "stripe_list_recent_charges"]
    assert tool_lines[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_refund_charge_admin_succeeds(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
    captured_requests: list[httpx.Request],
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"id": "re_x", "status": "succeeded"})

    outcome = await dispatcher.dispatch(
        tool_name="stripe_refund_charge",
        arguments={"charge_id": "ch_abc"},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == {"id": "re_x", "status": "succeeded"}

    # Ensure form-encoded body even via dispatcher.
    refund_req = next(r for r in captured_requests if r.method == "POST")
    assert refund_req.headers["content-type"].startswith("application/x-www-form-urlencoded")


@pytest.mark.asyncio
async def test_dispatch_refund_charge_rejects_ops_caller_no_post(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
    captured_requests: list[httpx.Request],
) -> None:
    """`stripe_refund_charge` is admin-scoped — ops caller gets `forbidden_tool`,
    and CRITICALLY no HTTP POST is issued (auth blocks pre-invocation)."""
    outcome = await dispatcher.dispatch(
        tool_name="stripe_refund_charge",
        arguments={"charge_id": "ch_abc", "amount_cents": 500},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"
    # No HTTP at all — auth gate stops the call before invocation.
    assert captured_requests == []
    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "stripe_refund_charge"]
    assert tool_lines[0]["status"] == "forbidden_tool"
    assert tool_lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_keychain_miss_audited(
    monkeypatch: pytest.MonkeyPatch,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Keychain miss surfaces as `keychain_miss` in audit log."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    outcome = await dispatcher.dispatch(
        tool_name="stripe_list_customers",
        arguments={},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "keychain_miss"
    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "stripe_list_customers"]
    assert tool_lines[0]["status"] == "keychain_miss"


# -- token leak guard -----------------------------------------------------


@pytest.mark.asyncio
async def test_token_never_leaks_to_audit_log(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Audit JSONL must NEVER contain the token string under any circumstance."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"data": [{"id": "cus_1"}]})

    await dispatcher.dispatch(
        tool_name="stripe_list_customers",
        arguments={"email": "x@y.com"},
        presented_token="OPS_TOK",
    )
    await dispatcher.dispatch(
        tool_name="stripe_get_subscription",
        arguments={"subscription_id": "sub_1"},
        presented_token="OPS_TOK",
    )

    raw = (tmp_path / "audit.log").read_text()
    assert FAKE_TEST_TOKEN not in raw, "Token leaked into audit log!"
    assert "Bearer" not in raw, "Authorization header leaked into audit log!"


def test_no_token_in_any_request_url_or_body(
    fake_token: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Cross-tool sweep — call every tool, assert token only in headers, never URL/body."""

    def _responder(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "id": "ok"})

    install_mock_transport["responder"] = _responder

    stripe_tools.stripe_list_customers(limit=2, email="user@example.com")
    stripe_tools.stripe_get_subscription(subscription_id="sub_1")
    stripe_tools.stripe_list_recent_charges(limit=2)
    stripe_tools.stripe_refund_charge(charge_id="ch_1", amount_cents=500)

    for req in captured_requests:
        assert FAKE_TEST_TOKEN not in str(req.url), f"token leaked in URL: {req.url}"
        body = req.content.decode("utf-8") if req.content else ""
        assert FAKE_TEST_TOKEN not in body, f"token leaked in body: {body!r}"
        assert req.headers["Authorization"] == f"Bearer {FAKE_TEST_TOKEN}"


# -- registration metadata -------------------------------------------------


def test_all_tools_registered_with_correct_scopes() -> None:
    """`register_all()` puts the 4 tools into the global registry with right scopes.

    Per ARCH § Module 5 :
    * read tools (list_customers, get_subscription, list_recent_charges) → ops.
    * mutating refund_charge → admin.
    """
    stripe_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    expected = {
        "stripe_list_customers": "ops",
        "stripe_get_subscription": "ops",
        "stripe_list_recent_charges": "ops",
        "stripe_refund_charge": "admin",
    }
    for name, scope in expected.items():
        assert name in TOOLS_REGISTRY, f"missing {name}"
        assert TOOLS_REGISTRY[name].scope == scope, (
            f"{name} declared scope {TOOLS_REGISTRY[name].scope!r}, expected {scope!r}"
        )


def test_register_all_is_idempotent() -> None:
    """Calling `register_all()` twice doesn't raise."""
    stripe_tools.register_all()
    stripe_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    assert "stripe_list_customers" in TOOLS_REGISTRY


def test_load_builtin_tools_includes_stripe() -> None:
    """`server.load_builtin_tools()` is the production entry point — must wire Stripe."""
    from agent_mcp.server import TOOLS_REGISTRY, load_builtin_tools

    load_builtin_tools()
    for name in (
        "stripe_list_customers",
        "stripe_get_subscription",
        "stripe_list_recent_charges",
        "stripe_refund_charge",
    ):
        assert name in TOOLS_REGISTRY
