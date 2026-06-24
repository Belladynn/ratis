"""Shared fixtures for ratis-agent-mcp tests.

Foundation tests are hermetic — they never call the real macOS Keychain,
never write to the real `~/.local/state/...` audit log, and never spawn a
real MCP SDK server. The fixtures here provide the isolation primitives.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_mcp import audit as audit_mod
from agent_mcp import keychain as keychain_mod
from agent_mcp import server as server_mod


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME, XDG_CONFIG_HOME and XDG_STATE_HOME inside `tmp_path`.

    Any module that resolves config / state paths via `agent_mcp.config`
    will land under `tmp_path/...` for the duration of the test, leaving
    the real user environment untouched.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    # Clear any leftover env vars from the parent shell that would alias the
    # caller token — tests that need them should set them explicitly.
    for var in ("MCP_AUTH_ADMIN_TOKEN", "MCP_AUTH_OPS_TOKEN", "MCP_AUTH_TOKEN", "MCP_AUDIT_LOG_PATH"):
        monkeypatch.delenv(var, raising=False)
    return fake_home


@pytest.fixture
def audit_log(tmp_path: Path) -> audit_mod.AuditLog:
    """Audit log writer pointed at `tmp_path/audit.log` — fresh per test."""
    return audit_mod.AuditLog(tmp_path / "audit.log")


@pytest.fixture
def fake_security_runner() -> tuple[Callable[..., subprocess.CompletedProcess[Any]], dict[str, Any]]:
    """Return a (`runner`, `state`) pair to inject into `Keychain(runner=...)`.

    `state["store"]` simulates the per-account secret table. The runner
    interprets the argv of `security` invocations enough to satisfy the
    Keychain's expectations (find / add / delete generic password). Tests
    can pre-populate `state["store"]` to test reads, or invoke the runner
    indirectly via `Keychain.set()` to test the full round-trip.
    """
    state: dict[str, Any] = {
        "store": {},  # account -> value
        "calls": [],  # list of argv lists for assertions
    }

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        state["calls"].append(argv)
        # Command shape : `security <verb> -s <service> -a <account> [-w] [...]`
        if (
            argv[:2] != ["security", "find-generic-password"]
            and argv[:2]
            != [
                "security",
                "add-generic-password",
            ]
            and argv[:2] != ["security", "delete-generic-password"]
        ):
            return subprocess.CompletedProcess(argv, 1, "", "unknown command")

        # Extract -a and -s.
        account = None
        for i, tok in enumerate(argv):
            if tok == "-a":
                account = argv[i + 1]

        if argv[1] == "find-generic-password":
            if account in state["store"]:
                return subprocess.CompletedProcess(argv, 0, state["store"][account] + "\n", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        if argv[1] == "add-generic-password":
            # The Keychain wrapper passes the secret on stdin via the
            # `input=` kwarg. We mirror that.
            value = kwargs.get("input")
            if not value:
                return subprocess.CompletedProcess(argv, 1, "", "no stdin")
            state["store"][account] = value
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "delete-generic-password":
            if account in state["store"]:
                del state["store"][account]
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        return subprocess.CompletedProcess(argv, 1, "", "unhandled")

    return runner, state


@pytest.fixture
def fake_keychain(
    fake_security_runner: tuple[Callable[..., subprocess.CompletedProcess[Any]], dict[str, Any]],
) -> tuple[keychain_mod.Keychain, dict[str, Any]]:
    """`Keychain` wired against the fake security runner ; cache TTL=60s."""
    runner, state = fake_security_runner
    return keychain_mod.Keychain(runner=runner), state


@pytest.fixture(autouse=True)
def reset_tools_registry() -> Iterator[None]:
    """Foundation tests register/clear tools — keep the registry clean."""
    server_mod.clear_registry()
    yield
    server_mod.clear_registry()


@pytest.fixture(autouse=True)
def reset_db_throttles() -> Iterator[None]:
    """db_query (60/min) et db_propose_write (10/min) deques in-memory.
    Reset entre tests pour éviter qu'une accumulation cross-tests trip
    le rate-limit d'un test sans rapport.
    """
    from agent_mcp.tools import db_tools as _db_tools

    _db_tools._reset_throttle_for_tests()
    _db_tools._reset_propose_write_throttle_for_tests()
    yield
    _db_tools._reset_throttle_for_tests()
    _db_tools._reset_propose_write_throttle_for_tests()
