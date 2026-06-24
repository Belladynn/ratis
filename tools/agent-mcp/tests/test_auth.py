"""Tests for `agent_mcp.auth.AuthGate`."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_mcp.auth import ADMIN_ENV, OPS_ENV, AuthGate
from agent_mcp.errors import ForbiddenTool


@pytest.fixture
def gate_from_kwargs() -> AuthGate:
    """Auth gate constructed from explicit token kwargs (no file IO)."""
    return AuthGate(admin_token="ADMIN_X", ops_token="OPS_Y")


def test_resolve_caller_admin(gate_from_kwargs: AuthGate) -> None:
    assert gate_from_kwargs.resolve_caller("ADMIN_X") == "admin"


def test_resolve_caller_ops(gate_from_kwargs: AuthGate) -> None:
    assert gate_from_kwargs.resolve_caller("OPS_Y") == "ops"


def test_resolve_caller_unknown_token_raises(gate_from_kwargs: AuthGate) -> None:
    with pytest.raises(ForbiddenTool):
        gate_from_kwargs.resolve_caller("WHO_AM_I")


def test_resolve_caller_missing_token_raises(gate_from_kwargs: AuthGate) -> None:
    with pytest.raises(ForbiddenTool):
        gate_from_kwargs.resolve_caller(None)
    with pytest.raises(ForbiddenTool):
        gate_from_kwargs.resolve_caller("")


def test_check_scope_both_accepts_either() -> None:
    AuthGate.check_scope("admin", "both")
    AuthGate.check_scope("ops", "both")


def test_check_scope_ops_accepts_admin_caller() -> None:
    """Admin > ops in the hierarchy — admin can call ops-tagged tools."""
    AuthGate.check_scope("admin", "ops")


def test_check_scope_ops_accepts_ops_caller() -> None:
    AuthGate.check_scope("ops", "ops")


def test_check_scope_admin_rejects_ops_caller() -> None:
    with pytest.raises(ForbiddenTool):
        AuthGate.check_scope("ops", "admin")


def test_check_scope_admin_accepts_admin_caller() -> None:
    AuthGate.check_scope("admin", "admin")


def test_load_from_tokens_env_file(tmp_path: Path) -> None:
    """A `tokens.env` file fully wires both admin and ops tokens."""
    path = tmp_path / "tokens.env"
    path.write_text(
        f"# header comment\n{ADMIN_ENV}=ADM_FILE\n{OPS_ENV}=OPS_FILE\n",
        encoding="utf-8",
    )
    gate = AuthGate(tokens_path=path)
    assert gate.resolve_caller("ADM_FILE") == "admin"
    assert gate.resolve_caller("OPS_FILE") == "ops"


def test_kwarg_tokens_override_file(tmp_path: Path) -> None:
    """Explicit kwargs win over env file (test-friendly precedence)."""
    path = tmp_path / "tokens.env"
    path.write_text(f"{ADMIN_ENV}=FILE_ADMIN\n{OPS_ENV}=FILE_OPS\n", encoding="utf-8")
    gate = AuthGate(admin_token="OVERRIDE", tokens_path=path)
    assert gate.resolve_caller("OVERRIDE") == "admin"
    # Ops still comes from the file because we didn't override it.
    assert gate.resolve_caller("FILE_OPS") == "ops"


def test_env_vars_override_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Process env vars override file values (typical CI override pattern)."""
    path = tmp_path / "tokens.env"
    path.write_text(f"{ADMIN_ENV}=FILE_ADMIN\n{OPS_ENV}=FILE_OPS\n", encoding="utf-8")
    monkeypatch.setenv(ADMIN_ENV, "ENV_ADMIN")
    gate = AuthGate(tokens_path=path)
    assert gate.resolve_caller("ENV_ADMIN") == "admin"
    assert gate.resolve_caller("FILE_OPS") == "ops"


def test_presented_token_from_env_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    gate = AuthGate(admin_token="A", ops_token="O")
    assert gate.presented_token_from_env() is None


def test_presented_token_from_env_reads_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "from-env")
    gate = AuthGate(admin_token="A", ops_token="O")
    assert gate.presented_token_from_env() == "from-env"


def test_invalid_scope_value_rejects() -> None:
    """Defensive — unexpected scope strings should not silently pass."""
    with pytest.raises(ForbiddenTool):
        AuthGate.check_scope("admin", "godmode")  # type: ignore[arg-type]


def test_constant_time_compare_does_not_leak_role_order() -> None:
    """Functional smoke that both tokens are checked regardless of order.

    We can't measure timing reliably in unit tests but we can at least
    assert both tokens are accepted and mismatches are uniformly rejected.
    """
    gate = AuthGate(admin_token="A" * 32, ops_token="O" * 32)
    assert gate.resolve_caller("A" * 32) == "admin"
    assert gate.resolve_caller("O" * 32) == "ops"
    with pytest.raises(ForbiddenTool):
        gate.resolve_caller("X" * 32)
