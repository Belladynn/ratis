"""Tests for `agent_mcp.keychain.Keychain` (mocked subprocess)."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from agent_mcp.errors import KeychainMiss
from agent_mcp.keychain import Keychain


def test_get_returns_value(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    kc, state = fake_keychain
    state["store"]["sentry"] = "sk_test_xxx"
    assert kc.get("sentry") == "sk_test_xxx"


def test_get_strips_trailing_newline(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    """`security` returns the value followed by `\\n` — runtime must strip."""
    kc, state = fake_keychain
    state["store"]["sentry"] = "no-newline-after-this"
    val = kc.get("sentry")
    # Our fake runner appends a `\n` to mimic real `security` ; the wrapper
    # must strip exactly that one newline.
    assert val == "no-newline-after-this"
    assert "\n" not in val


def test_get_missing_raises_keychain_miss(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    kc, _ = fake_keychain
    with pytest.raises(KeychainMiss):
        kc.get("never-stored")


def test_get_caches_within_ttl(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    """Two consecutive `get` calls produce one subprocess invocation."""
    kc, state = fake_keychain
    state["store"]["sentry"] = "v1"
    kc.get("sentry")
    kc.get("sentry")
    find_calls = [c for c in state["calls"] if c[1] == "find-generic-password"]
    assert len(find_calls) == 1, f"expected 1 read, got {len(find_calls)}"


def test_get_cache_can_be_invalidated(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    kc, state = fake_keychain
    state["store"]["sentry"] = "v1"
    kc.get("sentry")
    kc.invalidate_cache("sentry")
    state["store"]["sentry"] = "v2"
    assert kc.get("sentry") == "v2"


def test_set_round_trip(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    kc, state = fake_keychain
    kc.set("eas", "expo-token-abc")
    assert state["store"]["eas"] == "expo-token-abc"
    # Cache must be invalidated by `set` so a follow-up `get` re-reads.
    assert kc.get("eas") == "expo-token-abc"


def test_set_does_not_pass_secret_in_argv(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    """Critical security invariant — secret value must NEVER appear in argv."""
    kc, state = fake_keychain
    secret = "ULTRA-SECRET-VALUE"
    kc.set("github", secret)
    add_calls = [c for c in state["calls"] if c[1] == "add-generic-password"]
    assert len(add_calls) == 1
    assert secret not in add_calls[0], f"secret leaked into argv: {add_calls[0]}"


def test_set_empty_value_rejected(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    kc, _ = fake_keychain
    with pytest.raises(ValueError, match="non-empty"):
        kc.set("sentry", "")


def test_delete_removes_entry(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    kc, state = fake_keychain
    state["store"]["sentry"] = "to-delete"
    kc.delete("sentry")
    assert "sentry" not in state["store"]


def test_delete_missing_is_idempotent(
    fake_keychain: tuple[Keychain, dict[str, Any]],
) -> None:
    """Deleting a non-existent entry must not raise (44 = treated as success)."""
    kc, _ = fake_keychain
    kc.delete("never-existed")  # should not raise


def test_get_unexpected_exit_code_surfaces_as_miss() -> None:
    """Non-44 / non-0 exit codes are wrapped as `KeychainMiss` with stderr."""

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(argv, 51, "", "keychain locked")

    kc = Keychain(runner=runner)
    with pytest.raises(KeychainMiss, match="keychain locked"):
        kc.get("sentry")


def test_runner_invocation_uses_capture_output_and_text() -> None:
    """The wrapper must call `subprocess.run` with text=True + capture_output.

    We check this by inspecting the kwargs that hit the runner — easier
    than reverse-engineering subprocess internals.
    """
    captured: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 44, "", "")

    kc = Keychain(runner=runner)
    with pytest.raises(KeychainMiss):
        kc.get("anything")
    assert captured.get("text") is True
    assert captured.get("capture_output") is True
    assert captured.get("check") is False


def test_default_runner_is_subprocess_run() -> None:
    """Sanity — without explicit runner, we point at subprocess.run.

    We don't actually invoke it (would hit real Keychain on macOS / fail on
    Linux). We just inspect the attribute.
    """
    kc = Keychain()
    assert kc._runner is subprocess.run
