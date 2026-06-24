"""TDD coverage for `agent_mcp.tools.glitchtip_tools`.

Strategy
--------
* The 4 GlitchTip tools are pure Python functions wrapping `httpx.Client` calls.
* We inject an `httpx.MockTransport` so no real network is touched and we can
  assert exactly what HTTP request the tool issued (URL, headers, body).
* `Keychain.get` is monkeypatched to return a fake token — never the real one.
* The audit assertions go through the `Dispatcher` so we cover the full
  registration + dispatch + audit pipeline (the same code path Claude will
  exercise at runtime).

Token-leak guard (security-critical)
------------------------------------
At least one test reads back the audit JSONL and asserts the fake token
string never appears anywhere in the file — proving the redaction layer
plus the deliberate decision NOT to expose the token through tool args.

DA-47 follow-up — Sentry SaaS sunset, migration vers GlitchTip self-hosted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_mcp import keychain as keychain_mod
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.errors import KeychainMiss, ProviderError
from agent_mcp.server import Dispatcher
from agent_mcp.tools import glitchtip_tools

FAKE_TOKEN = "tok_unit_test_DO_NOT_LEAK"  # pragma: allowlist secret


# -- shared fixtures ------------------------------------------------------


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Patch `Keychain.get` so the GlitchTip tools see a fake token.

    The same fake value is returned every time — any test that wants to
    simulate `KeychainMiss` patches `get` again with `raises=`.
    """

    def _fake_get(self: keychain_mod.Keychain, account: str) -> str:
        assert account == "admin-glitchtip", f"unexpected keychain account {account!r}"
        return FAKE_TOKEN

    monkeypatch.setattr(keychain_mod.Keychain, "get", _fake_get)
    return FAKE_TOKEN


@pytest.fixture
def captured_requests() -> list[httpx.Request]:
    """List populated by the mock transport with every outbound request."""
    return []


@pytest.fixture
def install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    captured_requests: list[httpx.Request],
) -> Iterator[Any]:
    """Replace `glitchtip_tools._build_client` so it returns a client wired to a
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

    real_build = glitchtip_tools._build_client

    def fake_build_client(token: str) -> httpx.Client:
        # We still want the real header-setting logic to run — call the real
        # builder but swap the transport in afterwards. Simpler: build a new
        # Client mirroring the real configuration but using the mock transport.
        return httpx.Client(
            base_url=glitchtip_tools.GLITCHTIP_BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            transport=transport,
            timeout=glitchtip_tools.HTTP_TIMEOUT_SEC,
        )

    monkeypatch.setattr(glitchtip_tools, "_build_client", fake_build_client)
    yield state
    # Belt-and-braces : prevent the real builder from leaking out of fixture.
    glitchtip_tools._build_client = real_build  # type: ignore[assignment]


@pytest.fixture
def glitchtip_org(monkeypatch: pytest.MonkeyPatch) -> str:
    """Force `GLITCHTIP_ORG` to a deterministic value for URL assertions."""
    monkeypatch.setenv("GLITCHTIP_ORG", "ratis")
    return "ratis"


@pytest.fixture
def dispatcher(tmp_path: Path) -> Dispatcher:
    """Dispatcher backed by a temp audit log + admin/ops tokens.

    We register the GlitchTip tools by importing the module — the side-effect
    of decorator evaluation populates the global registry. The autouse
    `reset_tools_registry` fixture in `conftest.py` clears them between tests
    so we re-trigger registration here via a direct call.
    """
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    glitchtip_tools.register_all()  # idempotent — guarded with try/except
    return Dispatcher(auth=auth, audit=audit)


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# -- happy paths ----------------------------------------------------------


def test_list_issues_happy_path(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`glitchtip_list_issues` issues a GET to the project endpoint and returns the JSON list."""
    fake_response = [
        {"id": "1", "title": "TypeError: x", "status": "unresolved"},
        {"id": "2", "title": "ValueError", "status": "unresolved"},
    ]
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = glitchtip_tools.glitchtip_list_issues(project="ratis-backend", limit=2)

    assert result == fake_response
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/0/projects/ratis/ratis-backend/issues/"
    assert req.url.params["query"] == "is:unresolved"
    assert req.url.params["limit"] == "2"
    assert req.headers["Authorization"] == f"Bearer {fake_token}"
    assert req.headers["Accept"] == "application/json"


def test_get_issue_happy_path(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    fake_response = {
        "id": "42",
        "title": "TypeError: cannot unpack",
        "metadata": {"value": "x"},
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = glitchtip_tools.glitchtip_get_issue(issue_id="42")

    assert result == fake_response
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/0/issues/42/"


def test_list_events_happy_path(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    fake_response = [{"eventID": "abc"}, {"eventID": "def"}]
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = glitchtip_tools.glitchtip_list_events(issue_id="42", limit=2)

    assert result == fake_response
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/0/issues/42/events/"
    assert req.url.params["limit"] == "2"


def test_resolve_issue_without_comment(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"id": "42", "status": "resolved"})

    result = glitchtip_tools.glitchtip_resolve_issue(issue_id="42")

    assert result == {"id": "42", "status": "resolved"}
    # Single request : the PUT. No comment POST.
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "PUT"
    assert req.url.path == "/api/0/issues/42/"
    body = json.loads(req.content.decode("utf-8"))
    assert body == {"status": "resolved"}


def test_resolve_issue_with_comment_posts_comment_too(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """When `comment` is non-empty, a second POST to /comments/ is sent."""

    def _responder(req: httpx.Request) -> httpx.Response:
        if req.method == "PUT":
            return httpx.Response(200, json={"id": "42", "status": "resolved"})
        if req.method == "POST" and req.url.path.endswith("/comments/"):
            return httpx.Response(201, json={"id": "c1", "data": {"text": "fixed in PR #299"}})
        return httpx.Response(404, json={"detail": "unexpected"})

    install_mock_transport["responder"] = _responder

    result = glitchtip_tools.glitchtip_resolve_issue(issue_id="42", comment="fixed in PR #299")

    # The PUT response is what we surface — the comment POST is a side-effect.
    assert result["status"] == "resolved"
    assert len(captured_requests) == 2
    methods = {r.method for r in captured_requests}
    assert methods == {"PUT", "POST"}
    comment_req = next(r for r in captured_requests if r.method == "POST")
    body = json.loads(comment_req.content.decode("utf-8"))
    assert body == {"text": "fixed in PR #299"}


def test_default_org_is_ratis(
    fake_token: str,
    monkeypatch: pytest.MonkeyPatch,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """When `GLITCHTIP_ORG` is not set, default org is `ratis` (per ARCH § Module 1)."""
    monkeypatch.delenv("GLITCHTIP_ORG", raising=False)
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[])

    glitchtip_tools.glitchtip_list_issues(project="anything")

    assert "/api/0/projects/ratis/anything/issues/" in str(captured_requests[0].url)


# -- error paths ----------------------------------------------------------


def test_missing_token_raises_keychain_miss(
    monkeypatch: pytest.MonkeyPatch,
    glitchtip_org: str,
) -> None:
    """When the keychain entry is absent, the tool surfaces `KeychainMiss`."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="admin-glitchtip"):
        glitchtip_tools.glitchtip_list_issues(project="anything")


def test_provider_4xx_raises_provider_error(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
) -> None:
    """GlitchTip returning 4xx is wrapped in `ProviderError`."""
    install_mock_transport["responder"] = lambda req: httpx.Response(404, json={"detail": "issue not found"})

    with pytest.raises(ProviderError, match="404"):
        glitchtip_tools.glitchtip_get_issue(issue_id="does-not-exist")


def test_provider_5xx_raises_provider_error(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(503, text="upstream down")

    with pytest.raises(ProviderError, match="503"):
        glitchtip_tools.glitchtip_list_issues(project="anything")


# -- registration & dispatch (full pipeline) ------------------------------


@pytest.mark.asyncio
async def test_dispatch_list_issues_audits_ok(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """End-to-end : call via dispatcher, audit line records ok status."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[{"id": "1"}])

    outcome = await dispatcher.dispatch(
        tool_name="glitchtip_list_issues",
        arguments={"project": "ratis-backend", "limit": 1},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == [{"id": "1"}]

    lines = _audit_lines(tmp_path / "audit.log")
    assert len(lines) == 1
    assert lines[0]["tool"] == "glitchtip_list_issues"
    assert lines[0]["status"] == "ok"
    assert lines[0]["caller"] == "ops"
    # Args do NOT contain the token (the tool reads from Keychain — the token
    # never enters the args dict, so redaction is structural, not just by name).
    assert "token" not in lines[0]["args_redacted"]
    assert "Authorization" not in lines[0]["args_redacted"]


@pytest.mark.asyncio
async def test_dispatch_resolve_issue_rejects_ops_caller(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
    captured_requests: list[httpx.Request],
) -> None:
    """`glitchtip_resolve_issue` is admin-scoped — ops caller gets `forbidden_tool`."""
    outcome = await dispatcher.dispatch(
        tool_name="glitchtip_resolve_issue",
        arguments={"issue_id": "42"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"
    # No HTTP call should have happened — auth blocks before invocation.
    assert captured_requests == []
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "forbidden_tool"
    assert lines[0]["tool"] == "glitchtip_resolve_issue"
    assert lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_resolve_issue_admin_succeeds(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json={"id": "42", "status": "resolved"})

    outcome = await dispatcher.dispatch(
        tool_name="glitchtip_resolve_issue",
        arguments={"issue_id": "42"},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == {"id": "42", "status": "resolved"}


@pytest.mark.asyncio
async def test_dispatch_keychain_miss_audited(
    monkeypatch: pytest.MonkeyPatch,
    glitchtip_org: str,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Keychain miss surfaces as `keychain_miss` in audit log (status from `KeychainMiss`)."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    outcome = await dispatcher.dispatch(
        tool_name="glitchtip_list_issues",
        arguments={"project": "anything"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "keychain_miss"
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "keychain_miss"


# -- token leak guard -----------------------------------------------------


@pytest.mark.asyncio
async def test_token_never_leaks_to_audit_log(
    fake_token: str,
    glitchtip_org: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Audit JSONL must NEVER contain the token string under any circumstance."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[{"id": "1", "title": "boom"}])

    await dispatcher.dispatch(
        tool_name="glitchtip_list_issues",
        arguments={"project": "anything"},
        presented_token="OPS_TOK",
    )
    await dispatcher.dispatch(
        tool_name="glitchtip_get_issue",
        arguments={"issue_id": "1"},
        presented_token="OPS_TOK",
    )

    raw = (tmp_path / "audit.log").read_text()
    assert FAKE_TOKEN not in raw, "Token leaked into audit log!"
    assert "Bearer" not in raw, "Authorization header leaked into audit log!"


# -- registration metadata -------------------------------------------------


def test_all_tools_registered_with_correct_scopes() -> None:
    """`register_all()` puts the 4 tools into the global registry with right scopes."""
    glitchtip_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    expected = {
        "glitchtip_list_issues": "ops",
        "glitchtip_get_issue": "ops",
        "glitchtip_list_events": "ops",
        "glitchtip_resolve_issue": "admin",
    }
    for name, scope in expected.items():
        assert name in TOOLS_REGISTRY, f"missing {name}"
        assert TOOLS_REGISTRY[name].scope == scope, (
            f"{name} declared scope {TOOLS_REGISTRY[name].scope!r}, expected {scope!r}"
        )


def test_register_all_is_idempotent() -> None:
    """Calling `register_all()` twice doesn't raise (re-importing the module is safe)."""
    glitchtip_tools.register_all()
    glitchtip_tools.register_all()  # would raise ValueError without idempotence guard
    from agent_mcp.server import TOOLS_REGISTRY

    assert "glitchtip_list_issues" in TOOLS_REGISTRY


def test_load_builtin_tools_includes_glitchtip() -> None:
    """`server.load_builtin_tools()` is the production entry point — must wire GlitchTip."""
    from agent_mcp.server import TOOLS_REGISTRY, load_builtin_tools

    load_builtin_tools()
    for name in (
        "glitchtip_list_issues",
        "glitchtip_get_issue",
        "glitchtip_list_events",
        "glitchtip_resolve_issue",
    ):
        assert name in TOOLS_REGISTRY
