"""Tests for the `agent-mcp` CLI (no real subprocess / no real keychain)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agent_mcp import cli
from agent_mcp.auth import ADMIN_ENV, OPS_ENV
from agent_mcp.config import audit_log_file, tokens_file


def _install_fake_keychain_runner(
    monkeypatch: pytest.MonkeyPatch,
    fake_runner: Any,
) -> None:
    """Patch `Keychain.__init__` so all instances use `fake_runner`.

    Linux CI has no `security` CLI (KP-58) — every test that exercises the
    Keychain MUST inject a runner. This helper centralises the patch shape
    used across multiple tests in this file.
    """
    from agent_mcp import keychain as keychain_mod

    real_init = keychain_mod.Keychain.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("runner", fake_runner)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(keychain_mod.Keychain, "__init__", patched_init)


def test_init_creates_tokens_env_with_mode_600(isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["init"])
    assert rc == 0

    path = tokens_file()
    assert path.exists()
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    body = path.read_text(encoding="utf-8")
    assert ADMIN_ENV in body
    assert OPS_ENV in body

    out = capsys.readouterr().out
    # Both tokens must be displayed once.
    assert ADMIN_ENV in out
    assert OPS_ENV in out


def test_init_generates_unique_tokens_per_role(isolated_home: Path) -> None:
    cli.main(["init"])
    body = tokens_file().read_text(encoding="utf-8")
    admin_line = next(line for line in body.splitlines() if line.startswith(ADMIN_ENV + "="))
    ops_line = next(line for line in body.splitlines() if line.startswith(OPS_ENV + "="))
    admin_value = admin_line.split("=", 1)[1]
    ops_value = ops_line.split("=", 1)[1]
    assert admin_value != ops_value
    # 32 bytes urlsafe ≈ 43 base64-url characters (no padding).
    assert len(admin_value) >= 40
    assert len(ops_value) >= 40


def test_init_refuses_to_overwrite_existing_file(isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["init"])
    rc = cli.main(["init"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists" in err


def test_tokens_rotate_admin_writes_new_token(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli.main(["init"])
    body_before = tokens_file().read_text(encoding="utf-8")

    rc = cli.main(["tokens", "rotate", "--role", "admin"])
    assert rc == 0

    body_after = tokens_file().read_text(encoding="utf-8")
    # Admin line must change.
    admin_before = next(line for line in body_before.splitlines() if line.startswith(ADMIN_ENV + "="))
    admin_after = next(line for line in body_after.splitlines() if line.startswith(ADMIN_ENV + "="))
    assert admin_before != admin_after
    # Ops line must NOT change.
    ops_before = next(line for line in body_before.splitlines() if line.startswith(OPS_ENV + "="))
    ops_after = next(line for line in body_after.splitlines() if line.startswith(OPS_ENV + "="))
    assert ops_before == ops_after


def test_tokens_rotate_writes_audit_line(
    isolated_home: Path,
) -> None:
    cli.main(["init"])
    cli.main(["tokens", "rotate", "--role", "ops"])

    audit_path = audit_log_file()
    assert audit_path.exists()
    lines = [json.loads(ln) for ln in audit_path.read_text().splitlines() if ln]
    assert any(rec["status"] == "token_rotated" and rec["args_redacted"].get("role") == "ops" for rec in lines)


def test_tokens_rotate_without_init_fails(isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["tokens", "rotate", "--role", "admin"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "tokens.env not found" in err


def test_keychain_set_invokes_keychain_via_stdin(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent-mcp keychain set sentry` must call the Keychain wrapper using
    stdin-piped input (the secret never appears in argv)."""
    captured: dict[str, Any] = {"calls": [], "store": {}}

    def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        captured["calls"].append({"argv": argv, "kwargs": kwargs})
        verb = argv[1]
        # Find -a value.
        a_idx = argv.index("-a") + 1
        account = argv[a_idx]
        if verb == "find-generic-password":
            if account in captured["store"]:
                return subprocess.CompletedProcess(argv, 0, captured["store"][account] + "\n", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        if verb == "add-generic-password":
            captured["store"][account] = kwargs["input"]
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    # Patch the Keychain default runner.
    from agent_mcp import keychain as keychain_mod

    real_init = keychain_mod.Keychain.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("runner", fake_runner)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(keychain_mod.Keychain, "__init__", patched_init)
    monkeypatch.setattr("agent_mcp.cli.getpass.getpass", lambda _prompt="": "TOP-SECRET")

    rc = cli.main(["keychain", "set", "sentry"])
    assert rc == 0
    # The secret must NOT have been passed in argv on the add call.
    add_calls = [c for c in captured["calls"] if c["argv"][1] == "add-generic-password"]
    assert len(add_calls) == 1
    assert "TOP-SECRET" not in add_calls[0]["argv"]
    # And the secret was sent via stdin.
    assert add_calls[0]["kwargs"]["input"] == "TOP-SECRET"
    assert captured["store"]["sentry"] == "TOP-SECRET"


def test_keychain_set_empty_value_aborts(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a fake runner so the test runs on Linux CI (no `security` CLI on
    # Docker runners — would raise FileNotFoundError before the empty-value
    # check fires). The runner returns "not found" so cmd_keychain_set takes
    # the KeychainMiss branch and reaches the empty-value validation.
    def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(argv, 44, "", "")  # 44 = not found

    from agent_mcp import keychain as keychain_mod

    real_init = keychain_mod.Keychain.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("runner", fake_runner)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(keychain_mod.Keychain, "__init__", patched_init)
    monkeypatch.setattr("agent_mcp.cli.getpass.getpass", lambda _prompt="": "")
    rc = cli.main(["keychain", "set", "sentry"])
    assert rc == 1


def test_keychain_rm_with_yes_flag_skips_confirm(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {"calls": []}

    def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        captured["calls"].append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    from agent_mcp import keychain as keychain_mod

    real_init = keychain_mod.Keychain.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("runner", fake_runner)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(keychain_mod.Keychain, "__init__", patched_init)
    rc = cli.main(["keychain", "rm", "sentry", "--yes"])
    assert rc == 0
    assert any(c[1] == "delete-generic-password" for c in captured["calls"])


def test_keychain_rm_without_yes_aborts_on_no(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    rc = cli.main(["keychain", "rm", "sentry"])
    assert rc == 1


def test_paths_command_smoke(isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`agent-mcp paths` runs and prints the resolved locations."""
    rc = cli.main(["paths"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tokens.env" in out
    assert "audit log" in out


def test_build_parser_has_all_subcommands() -> None:
    parser = cli.build_parser()
    # Smoke — `parse_args` raises SystemExit on missing args, which we suppress.
    for argv in (
        ["init"],
        ["serve"],
        ["paths"],
        ["keychain", "set", "x"],
        ["keychain", "rm", "x", "--yes"],
        ["tokens", "rotate", "--role", "admin"],
    ):
        ns = parser.parse_args(argv)
        assert hasattr(ns, "func")


def test_serve_subcommand_invokes_run_serve(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent-mcp serve` must call `server.run_serve` exactly once.

    We don't actually want to spin up an MCP runtime in tests — we patch the
    function and assert it was called.
    """
    called: dict[str, int] = {"n": 0}

    def fake_run_serve() -> None:
        called["n"] += 1

    # Patch the import target inside `cli.cmd_serve` (lazy import).
    import agent_mcp.server as server_mod

    monkeypatch.setattr(server_mod, "run_serve", fake_run_serve)
    # Force re-import inside cmd_serve to pick up the patched symbol.
    rc = cli.main(["serve"])
    assert rc == 0
    assert called["n"] == 1


# --- keychain check -------------------------------------------------------


def test_keychain_check_all_present_exits_zero(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`agent-mcp keychain check` exits 0 when every required account is present."""

    def fake_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        # Every find-generic-password lookup succeeds with a stub value.
        if argv[1] == "find-generic-password":
            return subprocess.CompletedProcess(argv, 0, "stub-secret\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unhandled")

    _install_fake_keychain_runner(monkeypatch, fake_runner)

    rc = cli.main(["keychain", "check"])
    assert rc == 0

    out = capsys.readouterr().out
    # All 8 accounts must be listed and reported as present.
    for account in cli.REQUIRED_PROVIDER_ACCOUNTS:
        assert account in out, f"missing account in output: {account}"
    assert "present" in out
    assert "missing" not in out


def test_keychain_check_some_missing_exits_one(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`agent-mcp keychain check` exits 1 when at least one account is missing."""
    # Half the accounts will return 44 (not found), half will return 0.
    missing_accounts = set(cli.REQUIRED_PROVIDER_ACCOUNTS[::2])  # alternate

    def fake_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if argv[1] != "find-generic-password":
            return subprocess.CompletedProcess(argv, 1, "", "unhandled")
        a_idx = argv.index("-a") + 1
        account = argv[a_idx]
        if account in missing_accounts:
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        return subprocess.CompletedProcess(argv, 0, "stub-secret\n", "")

    _install_fake_keychain_runner(monkeypatch, fake_runner)

    rc = cli.main(["keychain", "check"])
    assert rc == 1

    out = capsys.readouterr().out
    # Mix of present and missing should appear in the table output.
    assert "present" in out
    assert "missing" in out
    for account in cli.REQUIRED_PROVIDER_ACCOUNTS:
        assert account in out


# --- keychain get ---------------------------------------------------------


def test_keychain_get_existing_prints_value_no_newline(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`agent-mcp keychain get sentry` prints the secret raw on stdout (no label, no newline)."""
    expected = "TOP-SECRET-VALUE"

    def fake_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if argv[1] == "find-generic-password":
            return subprocess.CompletedProcess(argv, 0, expected + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    _install_fake_keychain_runner(monkeypatch, fake_runner)

    rc = cli.main(["keychain", "get", "sentry"])
    assert rc == 0

    captured = capsys.readouterr()
    # stdout must be exactly the secret, no trailing newline, no label.
    assert captured.out == expected
    # By default the warning is emitted on stderr.
    assert "agent-mcp" in captured.err
    assert "stdout" in captured.err


def test_keychain_get_missing_exits_one_clean_stderr(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing account → exit 1 + clean stderr message naming the account."""

    def fake_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(argv, 44, "", "not found")

    _install_fake_keychain_runner(monkeypatch, fake_runner)

    rc = cli.main(["keychain", "get", "sentry"])
    assert rc == 1

    captured = capsys.readouterr()
    # Nothing on stdout — we don't want a trailing empty line piped to a tool.
    assert captured.out == ""
    assert "sentry" in captured.err
    # The error must be unambiguous.
    assert "missing" in captured.err.lower() or "not found" in captured.err.lower()


def test_keychain_get_no_warn_silences_warning(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--no-warn` flag eliminates the default stderr warning."""
    expected = "secret-for-piping"

    def fake_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if argv[1] == "find-generic-password":
            return subprocess.CompletedProcess(argv, 0, expected + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    _install_fake_keychain_runner(monkeypatch, fake_runner)

    rc = cli.main(["keychain", "get", "sentry", "--no-warn"])
    assert rc == 0

    captured = capsys.readouterr()
    assert captured.out == expected
    assert captured.err == ""


# --- agent-mcp call -------------------------------------------------------


@pytest.fixture
def call_dispatcher_factory(tmp_path: Path) -> Any:
    """Return a factory that builds a Dispatcher with a known auth pair.

    Tests pass this factory through `cli._build_call_dispatcher` (override)
    to keep the in-process dispatcher hermetic — no real audit log under
    `~/.local/state/...` and no real provider HTTP.
    """
    from agent_mcp.audit import AuditLog
    from agent_mcp.auth import AuthGate
    from agent_mcp.server import Dispatcher

    def factory() -> Dispatcher:
        return Dispatcher(
            auth=AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK"),
            audit=AuditLog(tmp_path / "audit.log"),
        )

    return factory


def test_call_dispatches_to_registered_tool(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    call_dispatcher_factory: Any,
) -> None:
    """`agent-mcp call <tool> <json>` invokes the registered tool and prints JSON."""
    from agent_mcp.server import register_tool

    @register_tool(scope="ops")
    def echo_tool(x: int = 0, label: str = "hi") -> dict[str, Any]:
        """Echo the inputs back."""
        return {"echo": {"x": x, "label": label}}

    monkeypatch.setenv("MCP_AUTH_TOKEN", "OPS_TOK")
    monkeypatch.setattr(cli, "_build_call_dispatcher", call_dispatcher_factory)

    rc = cli.main(["call", "echo_tool", '{"x": 42, "label": "hello"}'])
    assert rc == 0

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == {"echo": {"x": 42, "label": "hello"}}


def test_call_unknown_tool_exits_one(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    call_dispatcher_factory: Any,
) -> None:
    """Unknown tool → exit 1 + clean stderr."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "OPS_TOK")
    monkeypatch.setattr(cli, "_build_call_dispatcher", call_dispatcher_factory)

    rc = cli.main(["call", "ghost_tool", "{}"])
    assert rc == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ghost_tool" in captured.err or "tool_not_registered" in captured.err


def test_call_forbidden_scope_exits_one(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    call_dispatcher_factory: Any,
) -> None:
    """Ops token calling admin-scoped tool → exit 1 + `forbidden_tool` on stderr."""
    from agent_mcp.server import register_tool

    @register_tool(scope="admin")
    def admin_tool() -> dict[str, str]:
        """Admin-only operation."""
        return {"ok": True}  # type: ignore[return-value]

    monkeypatch.setenv("MCP_AUTH_TOKEN", "OPS_TOK")
    monkeypatch.setattr(cli, "_build_call_dispatcher", call_dispatcher_factory)

    rc = cli.main(["call", "admin_tool", "{}"])
    assert rc == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "forbidden_tool" in captured.err


def test_call_invalid_json_args_exits_one(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    call_dispatcher_factory: Any,
) -> None:
    """Malformed JSON args → exit 1 + parse error on stderr (no provider call)."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "OPS_TOK")
    monkeypatch.setattr(cli, "_build_call_dispatcher", call_dispatcher_factory)

    rc = cli.main(["call", "any_tool", "{not json"])
    assert rc == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "json" in captured.err.lower()


def test_call_omitted_args_defaults_to_empty_dict(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    call_dispatcher_factory: Any,
) -> None:
    """`agent-mcp call my_tool` (no second arg) → tool invoked with `{}`."""
    from agent_mcp.server import register_tool

    captured_args: dict[str, Any] = {}

    @register_tool(scope="ops")
    def no_args_tool(**kwargs: Any) -> dict[str, Any]:
        """Echo back the kwargs we received."""
        captured_args.update(kwargs)
        return {"received": dict(kwargs)}

    monkeypatch.setenv("MCP_AUTH_TOKEN", "OPS_TOK")
    monkeypatch.setattr(cli, "_build_call_dispatcher", call_dispatcher_factory)

    rc = cli.main(["call", "no_args_tool"])
    assert rc == 0

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == {"received": {}}
    assert captured_args == {}
