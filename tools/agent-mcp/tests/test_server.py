"""Tests for the Dispatcher (MCP-SDK-agnostic core)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.server import Dispatcher, register_tool


@pytest.fixture
def dispatcher(tmp_path: Path) -> Dispatcher:
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    return Dispatcher(auth=auth, audit=audit)


def _audit_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_list_tools_empty_in_foundation(dispatcher: Dispatcher) -> None:
    """Foundation V0 ships an empty registry — list returns []."""
    assert dispatcher.list_tools() == []


@pytest.mark.asyncio
async def test_dispatch_unknown_token_audits_forbidden(dispatcher: Dispatcher, tmp_path: Path) -> None:
    outcome = await dispatcher.dispatch(
        tool_name="anything",
        arguments={"x": 1},
        presented_token="WRONG",
    )
    assert outcome.status == "forbidden_tool"
    assert outcome.caller is None
    lines = _audit_lines(tmp_path / "audit.log")
    assert len(lines) == 1
    assert lines[0]["status"] == "forbidden_tool"
    assert lines[0]["caller"] == "anonymous"


@pytest.mark.asyncio
async def test_dispatch_unregistered_tool_returns_not_registered(dispatcher: Dispatcher, tmp_path: Path) -> None:
    outcome = await dispatcher.dispatch(
        tool_name="ghost_tool",
        arguments={},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "tool_not_registered"
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "tool_not_registered"
    assert lines[0]["caller"] == "admin"


@pytest.mark.asyncio
async def test_dispatch_scope_mismatch_audits_forbidden(dispatcher: Dispatcher, tmp_path: Path) -> None:
    @register_tool(scope="admin")
    def admin_only_tool(payload: str = "x") -> str:
        """Pretend admin-only tool."""
        return payload

    outcome = await dispatcher.dispatch(
        tool_name="admin_only_tool",
        arguments={"payload": "hi"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"
    assert outcome.caller == "ops"
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "forbidden_tool"
    assert lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_ops_tool_succeeds_for_ops_caller(dispatcher: Dispatcher, tmp_path: Path) -> None:
    @register_tool(scope="ops")
    def ops_tool(name: str = "world") -> str:
        """Simple ops echo."""
        return f"hello {name}"

    outcome = await dispatcher.dispatch(
        tool_name="ops_tool",
        arguments={"name": "ratis"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == "hello ratis"
    assert outcome.caller == "ops"
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "ok"
    assert lines[0]["tool"] == "ops_tool"


@pytest.mark.asyncio
async def test_admin_can_call_ops_tool(dispatcher: Dispatcher) -> None:
    """Admin > ops — admin caller invokes ops-scoped tool fine."""

    @register_tool(scope="ops")
    def ops_tool() -> int:
        """Return 42."""
        return 42

    outcome = await dispatcher.dispatch(
        tool_name="ops_tool",
        arguments={},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == 42


@pytest.mark.asyncio
async def test_dispatch_async_tool(dispatcher: Dispatcher) -> None:
    @register_tool(scope="ops")
    async def async_tool(x: int = 1) -> int:
        """Async double."""
        return x * 2

    outcome = await dispatcher.dispatch(
        tool_name="async_tool",
        arguments={"x": 21},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == 42


@pytest.mark.asyncio
async def test_dispatch_provider_error_audited(dispatcher: Dispatcher, tmp_path: Path) -> None:
    @register_tool(scope="ops")
    def boom() -> None:
        """Tool that always blows up."""
        raise RuntimeError("upstream 500")

    outcome = await dispatcher.dispatch(
        tool_name="boom",
        arguments={},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "provider_error"
    assert "upstream 500" in (outcome.error or "")
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "provider_error"


@pytest.mark.asyncio
async def test_dispatch_redacts_secret_args_in_audit(dispatcher: Dispatcher, tmp_path: Path) -> None:
    @register_tool(scope="ops")
    def echo(token: str = "x", project: str = "y") -> str:
        """Echo args."""
        return f"{token}:{project}"

    await dispatcher.dispatch(
        tool_name="echo",
        arguments={"token": "very-secret", "project": "ratis"},
        presented_token="OPS_TOK",
    )
    lines = _audit_lines(tmp_path / "audit.log")
    args = lines[0]["args_redacted"]
    assert isinstance(args, dict)
    assert args["token"] == "<redacted>"
    assert args["project"] == "ratis"


def test_register_tool_captures_metadata() -> None:
    @register_tool(scope="admin")
    def my_tool(project: str, limit: int = 10) -> list[str]:
        """Short description.

        Longer detail follows but should not be in the description.
        """
        return [project] * limit

    from agent_mcp.server import TOOLS_REGISTRY

    entry = TOOLS_REGISTRY["my_tool"]
    assert entry.scope == "admin"
    assert entry.description == "Short description."
    assert entry.input_schema["type"] == "object"
    # Pydantic-derived schema should mention `project` and `limit`.
    properties = entry.input_schema.get("properties", {})
    assert "project" in properties
    assert "limit" in properties


def test_register_tool_duplicate_name_rejected() -> None:
    @register_tool(scope="ops")
    def first() -> int:
        """First."""
        return 1

    with pytest.raises(ValueError, match="already registered"):

        @register_tool(scope="ops")
        def first() -> int:
            """Second with same name."""
            return 2


@pytest.mark.asyncio
async def test_dispatcher_uses_module_registry_by_default(
    tmp_path: Path,
) -> None:
    """A dispatcher built without `registry=` sees newly-registered tools."""
    auth = AuthGate(admin_token="ADM", ops_token="OPS")
    audit = AuditLog(tmp_path / "audit.log")
    disp = Dispatcher(auth=auth, audit=audit)
    assert disp.list_tools() == []

    @register_tool(scope="ops")
    def added_after_construction() -> str:
        """New tool."""
        return "ok"

    # Registry mutation is visible through the live reference.
    names = [t["name"] for t in disp.list_tools()]
    assert "added_after_construction" in names
