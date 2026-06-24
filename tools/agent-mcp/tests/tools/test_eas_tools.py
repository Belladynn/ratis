"""TDD coverage for `agent_mcp.tools.eas_tools`.

Strategy
--------
* The 5 EAS tools wrap `eas-cli` (a Node.js CLI) via `subprocess.run([...])`.
* We monkeypatch `subprocess.run` so no real `eas` binary is invoked and we can
  assert exactly what argv the tool issued, what env it used, and what cwd it
  ran in.
* `Keychain.get` is monkeypatched to return a fake `EXPO_TOKEN` — never the
  real one. The test then asserts the fake token NEVER appears in the argv
  list (only in the `env=` kwarg passed to subprocess.run).
* For `eas_update_production`, the pre-publish gate calls `git rev-parse` —
  we route those `git` invocations through the same subprocess fake so we can
  control HEAD vs origin/main and assert the gate behaviour.

Token-leak guard (security-critical)
------------------------------------
Several tests assert :
* `EXPO_TOKEN=...` never appears as a string fragment in any captured argv;
* the fake token value never appears in the audit JSONL.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agent_mcp import keychain as keychain_mod
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.errors import KeychainMiss, ProviderError
from agent_mcp.server import Dispatcher
from agent_mcp.tools import eas_tools

FAKE_EXPO_TOKEN = "tok_eas_unit_test_DO_NOT_LEAK"  # pragma: allowlist secret

_FAKE_HEAD_SHA = "aaaaaaa1111111111111111111111111111aaaa"
_FAKE_ORIGIN_SHA = "aaaaaaa1111111111111111111111111111aaaa"
_FAKE_OTHER_SHA = "bbbbbbb2222222222222222222222222222bbbb"


# -- shared fixtures ------------------------------------------------------


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Patch `Keychain.get` so the EAS tools see a fake token under account 'eas'."""

    def _fake_get(self: keychain_mod.Keychain, account: str) -> str:
        assert account == "eas", f"unexpected keychain account {account!r}"
        return FAKE_EXPO_TOKEN

    monkeypatch.setattr(keychain_mod.Keychain, "get", _fake_get)
    return FAKE_EXPO_TOKEN


@pytest.fixture
def fake_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Replace `subprocess.run` (as seen by `eas_tools`) with a configurable fake.

    The returned `state` dict has :

    * ``calls`` — list of ``{"argv": [...], "kwargs": {...}}`` per call.
    * ``responder`` — callable ``(argv, kwargs) -> CompletedProcess`` ; default
      returns a successful empty-list JSON for `eas update:list` etc.
    * ``head_sha`` / ``origin_sha`` — what the fake returns for `git rev-parse`.
      By default they MATCH (HEAD == origin/main).
    """
    state: dict[str, Any] = {
        "calls": [],
        "head_sha": _FAKE_HEAD_SHA,
        "origin_sha": _FAKE_ORIGIN_SHA,
    }

    def _default_responder(argv: list[str], _kwargs: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        # Handle git rev-parse for the pre-publish gate.
        if argv[:2] == ["git", "rev-parse"]:
            target = argv[2] if len(argv) > 2 else "HEAD"
            sha = state["head_sha"] if target == "HEAD" else state["origin_sha"]
            return subprocess.CompletedProcess(argv, 0, sha + "\n", "")
        if argv[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        # Default eas-cli output : empty JSON list (covers update:list, build:list).
        if argv[:1] == ["eas"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        # Anything unexpected — fail loudly so tests notice unintended calls.
        return subprocess.CompletedProcess(argv, 127, "", f"unhandled fake: {argv!r}")

    state["responder"] = _default_responder

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        state["calls"].append({"argv": list(argv), "kwargs": dict(kwargs)})
        return state["responder"](argv, kwargs)

    monkeypatch.setattr(eas_tools.subprocess, "run", fake_run)
    return state


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force `RATIS_PROJECT_ROOT` to a tmp dir so tests don't depend on git."""
    monkeypatch.setenv("RATIS_PROJECT_ROOT", str(tmp_path))
    eas_tools._reset_project_root_cache()
    return tmp_path


@pytest.fixture
def dispatcher(tmp_path: Path) -> Dispatcher:
    """Dispatcher backed by a temp audit log + admin/ops tokens."""
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    eas_tools.register_all()
    return Dispatcher(auth=auth, audit=audit)


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _argv_list(state: dict[str, Any]) -> list[list[str]]:
    return [call["argv"] for call in state["calls"]]


def _eas_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [call for call in state["calls"] if call["argv"][:1] == ["eas"]]


# -- happy paths : read-only tools ----------------------------------------


def test_list_updates_happy_path(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """`eas_list_updates` invokes `eas update:list --branch=preview --limit=5 --json`."""
    fake_payload = [{"id": "upd-1", "message": "fix login"}]
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(argv, 0, json.dumps(fake_payload), "")

    result = eas_tools.eas_list_updates(channel="preview", limit=5)

    assert result == fake_payload
    eas_calls = _eas_calls(fake_subprocess)
    assert len(eas_calls) == 1
    argv = eas_calls[0]["argv"]
    assert argv[0] == "eas"
    assert argv[1] == "update:list"
    assert "--branch=preview" in argv or ("--branch" in argv and "preview" in argv)
    assert "--limit=5" in argv or ("--limit" in argv and "5" in argv)
    assert "--json" in argv
    assert "--non-interactive" in argv


def test_list_updates_token_in_env_not_argv(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """Token MUST NEVER appear in argv ; MUST appear in env dict only."""
    eas_tools.eas_list_updates(channel="preview")

    eas_calls = _eas_calls(fake_subprocess)
    assert eas_calls, "no eas subprocess call captured"
    call = eas_calls[0]
    # Argv inspection — every element must be a string with NO trace of token.
    for arg in call["argv"]:
        assert FAKE_EXPO_TOKEN not in arg, f"token leaked in argv: {arg!r}"
        assert "EXPO_TOKEN" not in arg, f"EXPO_TOKEN= leaked in argv: {arg!r}"
    # Env inspection — token present and bound to EXPO_TOKEN.
    env = call["kwargs"].get("env")
    assert env is not None, "env= was not passed to subprocess.run"
    assert env.get("EXPO_TOKEN") == FAKE_EXPO_TOKEN


def test_list_updates_runs_in_ratis_client_cwd(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """All eas commands must be invoked with cwd=<project_root>/ratis_client."""
    eas_tools.eas_list_updates(channel="preview")

    call = _eas_calls(fake_subprocess)[0]
    cwd = call["kwargs"].get("cwd")
    assert cwd is not None
    assert Path(cwd).name == "ratis_client"
    assert Path(cwd).parent == project_root


def test_list_builds_happy_path(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    fake_payload = [{"id": "bld-7", "platform": "android", "channel": "preview"}]
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(argv, 0, json.dumps(fake_payload), "")

    result = eas_tools.eas_list_builds(platform="android", limit=3)

    assert result == fake_payload
    argv = _eas_calls(fake_subprocess)[0]["argv"]
    assert argv[0] == "eas"
    assert argv[1] == "build:list"
    assert "--platform=android" in argv or ("--platform" in argv and "android" in argv)
    assert "--limit=3" in argv or ("--limit" in argv and "3" in argv)
    assert "--json" in argv
    assert "--non-interactive" in argv


# -- happy paths : mutating tools -----------------------------------------


def test_rollback_to_embedded_happy_path(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(
        argv, 0, json.dumps({"branch": "preview", "rolledBack": True}), ""
    )

    result = eas_tools.eas_rollback_to_embedded(channel="preview")

    assert isinstance(result, dict)
    assert result.get("rolledBack") is True
    argv = _eas_calls(fake_subprocess)[0]["argv"]
    assert argv[0] == "eas"
    assert argv[1] == "update:roll-back-to-embedded"
    assert "--channel" in argv
    assert "preview" in argv
    assert "--non-interactive" in argv


def test_update_preview_kp57_enforced(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """KP-57 hardcoded : argv MUST contain both --channel preview AND --environment preview."""
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(
        argv, 0, json.dumps({"id": "grp-1", "branch": "preview"}), ""
    )

    eas_tools.eas_update_preview(message="fix login")

    argv = _eas_calls(fake_subprocess)[0]["argv"]
    assert argv[0] == "eas"
    assert argv[1] == "update"
    # KP-57 : --channel and --environment MUST both be present and matching.
    assert "--channel" in argv
    chan_idx = argv.index("--channel")
    assert argv[chan_idx + 1] == "preview"
    assert "--environment" in argv
    env_idx = argv.index("--environment")
    assert argv[env_idx + 1] == "preview"
    # Non-interactive + JSON for parseable output.
    assert "--non-interactive" in argv
    # Message is passed via --message <value>.
    assert "--message" in argv
    msg_idx = argv.index("--message")
    assert argv[msg_idx + 1] == "fix login"


def test_update_preview_environment_override(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """`environment` kwarg overrides default but `--channel` stays `preview`."""
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(argv, 0, "{}", "")

    # Caller deliberately passes a different environment — preview channel still hard.
    eas_tools.eas_update_preview(message="x", environment="staging")

    argv = _eas_calls(fake_subprocess)[0]["argv"]
    chan_idx = argv.index("--channel")
    env_idx = argv.index("--environment")
    assert argv[chan_idx + 1] == "preview"
    assert argv[env_idx + 1] == "staging"


def test_update_production_pre_publish_gate_happy(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """When HEAD == origin/main, the gate passes and `eas update` runs."""
    # Default fake state has matching SHAs.
    fake_subprocess["head_sha"] = "deadbee" + ("0" * 33)
    fake_subprocess["origin_sha"] = "deadbee" + ("0" * 33)

    def _resp(argv: list[str], _kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "deadbee" + ("0" * 33) + "\n", "")
        if argv[:1] == ["eas"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "grp-prod"}), "")
        return subprocess.CompletedProcess(argv, 127, "", "unexpected")

    fake_subprocess["responder"] = _resp

    result = eas_tools.eas_update_production(message="release v1.2.3")

    assert isinstance(result, dict)
    eas_calls = _eas_calls(fake_subprocess)
    assert len(eas_calls) == 1
    argv = eas_calls[0]["argv"]
    chan_idx = argv.index("--channel")
    env_idx = argv.index("--environment")
    assert argv[chan_idx + 1] == "production"
    assert argv[env_idx + 1] == "production"
    # The git fetch + rev-parse happened BEFORE the eas call.
    git_calls = [c for c in fake_subprocess["calls"] if c["argv"][:1] == ["git"]]
    assert len(git_calls) >= 3  # fetch + rev-parse HEAD + rev-parse origin/main


def test_update_production_pre_publish_gate_rejects_dirty(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """When HEAD != origin/main, the gate raises and NO eas call is made."""
    fake_subprocess["head_sha"] = _FAKE_HEAD_SHA
    fake_subprocess["origin_sha"] = _FAKE_OTHER_SHA  # mismatch.

    with pytest.raises(RuntimeError, match="origin/main"):
        eas_tools.eas_update_production(message="dangerous")

    # Critical assertion : NO eas subprocess invoked when gate fails.
    eas_calls = _eas_calls(fake_subprocess)
    assert eas_calls == [], f"eas was invoked despite gate failure : {eas_calls!r}"


# -- error paths ----------------------------------------------------------


def test_keychain_miss_raises(
    monkeypatch: pytest.MonkeyPatch,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """Missing keychain entry surfaces `KeychainMiss` (no eas call attempted)."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="eas"):
        eas_tools.eas_list_updates(channel="preview")

    assert _eas_calls(fake_subprocess) == []


def test_eas_nonzero_exit_raises_provider_error(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """A non-zero exit from eas-cli is wrapped as `ProviderError`."""

    def _fail(argv: list[str], _kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, "", "Error: invalid token")

    fake_subprocess["responder"] = _fail

    with pytest.raises(ProviderError, match="invalid token"):
        eas_tools.eas_list_updates(channel="preview")


def test_eas_unparseable_json_raises_provider_error(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """If eas-cli returns success but garbled JSON, surface `ProviderError`."""
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(argv, 0, "not-json{{{", "")

    with pytest.raises(ProviderError, match="json"):
        eas_tools.eas_list_updates(channel="preview")


# -- registration & dispatch (full pipeline) ------------------------------


@pytest.mark.asyncio
async def test_dispatch_list_updates_audits_ok(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(
        argv, 0, json.dumps([{"id": "u1"}]), ""
    )

    outcome = await dispatcher.dispatch(
        tool_name="eas_list_updates",
        arguments={"channel": "preview", "limit": 1},
        presented_token="OPS_TOK",
    )

    assert outcome.status == "ok"
    assert outcome.result == [{"id": "u1"}]

    lines = _audit_lines(tmp_path / "audit.log")
    assert len(lines) == 1
    assert lines[0]["tool"] == "eas_list_updates"
    assert lines[0]["status"] == "ok"
    assert lines[0]["caller"] == "ops"
    # Token never enters args → never in audit.
    assert FAKE_EXPO_TOKEN not in json.dumps(lines[0])


@pytest.mark.asyncio
async def test_dispatch_update_production_rejects_ops_caller(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """`eas_update_production` is admin-scoped — ops caller is denied."""
    outcome = await dispatcher.dispatch(
        tool_name="eas_update_production",
        arguments={"message": "trying as ops"},
        presented_token="OPS_TOK",
    )

    assert outcome.status == "forbidden_tool"
    # No subprocess call should happen at all (auth gate runs before tool fn).
    assert fake_subprocess["calls"] == []
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "forbidden_tool"
    assert lines[0]["tool"] == "eas_update_production"


@pytest.mark.asyncio
async def test_dispatch_update_preview_rejects_ops_caller(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """`eas_update_preview` is also admin-scoped — ops caller is denied."""
    outcome = await dispatcher.dispatch(
        tool_name="eas_update_preview",
        arguments={"message": "trying as ops"},
        presented_token="OPS_TOK",
    )

    assert outcome.status == "forbidden_tool"


@pytest.mark.asyncio
async def test_dispatch_rollback_rejects_ops_caller(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """`eas_rollback_to_embedded` is admin-scoped — ops caller is denied."""
    outcome = await dispatcher.dispatch(
        tool_name="eas_rollback_to_embedded",
        arguments={"channel": "preview"},
        presented_token="OPS_TOK",
    )

    assert outcome.status == "forbidden_tool"


@pytest.mark.asyncio
async def test_dispatch_update_preview_admin_succeeds(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(
        argv, 0, json.dumps({"id": "grp-x"}), ""
    )

    outcome = await dispatcher.dispatch(
        tool_name="eas_update_preview",
        arguments={"message": "ota fix"},
        presented_token="ADMIN_TOK",
    )

    assert outcome.status == "ok"
    assert outcome.result == {"id": "grp-x"}


# -- token leak guard -----------------------------------------------------


@pytest.mark.asyncio
async def test_token_never_leaks_to_audit_log(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Audit JSONL must NEVER contain the EXPO_TOKEN value under any circumstance."""
    fake_subprocess["responder"] = lambda argv, _kw: subprocess.CompletedProcess(argv, 0, "[]", "")

    await dispatcher.dispatch(
        tool_name="eas_list_updates",
        arguments={"channel": "preview"},
        presented_token="OPS_TOK",
    )
    await dispatcher.dispatch(
        tool_name="eas_list_builds",
        arguments={"platform": "android"},
        presented_token="OPS_TOK",
    )

    raw = (tmp_path / "audit.log").read_text()
    assert FAKE_EXPO_TOKEN not in raw, "Token leaked into audit log!"
    assert "EXPO_TOKEN=" not in raw, "EXPO_TOKEN env line leaked into audit log!"


# -- registration metadata -------------------------------------------------


def test_all_tools_registered_with_correct_scopes() -> None:
    """`register_all()` puts the 5 tools into the global registry with right scopes."""
    eas_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    expected = {
        "eas_update_preview": "admin",
        "eas_update_production": "admin",
        "eas_list_updates": "ops",
        "eas_list_builds": "ops",
        "eas_rollback_to_embedded": "admin",
    }
    for name, scope in expected.items():
        assert name in TOOLS_REGISTRY, f"missing {name}"
        assert TOOLS_REGISTRY[name].scope == scope, (
            f"{name} declared scope {TOOLS_REGISTRY[name].scope!r}, expected {scope!r}"
        )


def test_register_all_is_idempotent() -> None:
    """Calling `register_all()` twice doesn't raise (re-importing the module is safe)."""
    eas_tools.register_all()
    eas_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    assert "eas_update_preview" in TOOLS_REGISTRY


def test_load_builtin_tools_includes_eas() -> None:
    """`server.load_builtin_tools()` is the production entry point — must wire EAS."""
    from agent_mcp.server import TOOLS_REGISTRY, load_builtin_tools

    load_builtin_tools()
    for name in (
        "eas_update_preview",
        "eas_update_production",
        "eas_list_updates",
        "eas_list_builds",
        "eas_rollback_to_embedded",
    ):
        assert name in TOOLS_REGISTRY


# -- argv hygiene final guard ---------------------------------------------


def test_no_token_in_any_argv_across_all_mutating_tools(
    fake_token: str,
    fake_subprocess: dict[str, Any],
    project_root: Path,
) -> None:
    """Cross-tool sweep — call every mutating tool, assert token never in argv."""
    fake_subprocess["responder"] = lambda argv, _kw: (
        subprocess.CompletedProcess(argv, 0, _FAKE_HEAD_SHA + "\n", "")
        if argv[:2] in (["git", "rev-parse"],)
        else subprocess.CompletedProcess(argv, 0, "{}", "")
    )

    eas_tools.eas_update_preview(message="x")
    eas_tools.eas_rollback_to_embedded(channel="preview")
    fake_subprocess["head_sha"] = "ffffff" + ("0" * 34)
    fake_subprocess["origin_sha"] = "ffffff" + ("0" * 34)
    eas_tools.eas_update_production(message="prod release")

    for call in fake_subprocess["calls"]:
        for arg in call["argv"]:
            assert FAKE_EXPO_TOKEN not in str(arg), f"token leaked in argv: {arg!r}"
            assert "EXPO_TOKEN=" not in str(arg)
        # Conversely, the env (when set) must carry the token.
        env = call["kwargs"].get("env")
        if env is not None and call["argv"][:1] == ["eas"]:
            assert env.get("EXPO_TOKEN") == FAKE_EXPO_TOKEN
