"""TDD coverage for `agent_mcp.secrets.cli_secrets` — ratis-secret CLI.

Strategy
--------
* Tests use monkeypatch to inject fake doubles (no real Keychain, no real subprocess).
* Verify output format for `list` and `audit` sub-commands.
* Verify `use` does NOT print the secret value to stdout.
* Verify `revoke` delegates to `secret_revoke` correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agent_mcp.secrets import cli_secrets

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    """Run the CLI and return (exit_code, stdout, stderr) via capsys workaround."""
    import io
    import sys

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = cli_secrets.main(args)
        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return rc, stdout, stderr


# ---------------------------------------------------------------------------
# test_cli_list_output_format
# ---------------------------------------------------------------------------


class TestCliList:
    def test_cli_list_output_format(self) -> None:
        """ratis-secret list should print name, category, version, issued_at."""
        fake_results = [
            {
                "name": "my-api-key",
                "category": "A",
                "version": 2,
                "lease_id": "lease-abc",
                "issued_at": "2026-05-01T10:00:00+00:00",
                "expires_at": None,
                "revoked_at": None,
            },
            {
                "name": "github-token",
                "category": "B",
                "version": 1,
                "lease_id": "lease-xyz",
                "issued_at": "2026-05-15T08:30:00+00:00",
                "expires_at": "2026-05-15T09:00:00+00:00",
                "revoked_at": None,
            },
        ]

        with patch("agent_mcp.secrets.cli_secrets.secret_list", return_value=fake_results):
            rc, stdout, _stderr = _run_cli(["list"])

        assert rc == 0
        assert "my-api-key" in stdout
        assert "cat=A" in stdout
        assert "v2" in stdout
        assert "2026-05-01" in stdout  # issued_at[:10]
        assert "github-token" in stdout
        assert "cat=B" in stdout
        # Secret values must NOT be in output (paranoid check)
        assert "lease-abc" not in stdout
        assert "lease-xyz" not in stdout


# ---------------------------------------------------------------------------
# test_cli_audit_output_format
# ---------------------------------------------------------------------------


class TestCliAudit:
    def test_cli_audit_output_format(self) -> None:
        """ratis-secret audit should print last N entries with ts, action, name."""
        fake_entries = [
            {
                "seq": 1,
                "ts": "2026-05-01T10:00:00+00:00",
                "action": "generate",
                "name": "my-secret",
                "principal": "agent",
            },
            {
                "seq": 2,
                "ts": "2026-05-01T10:05:00+00:00",
                "action": "get",
                "name": "my-secret",
                "principal": "agent",
            },
        ]

        mock_chain = MagicMock()
        mock_chain.tail.return_value = fake_entries

        with patch("agent_mcp.secrets.cli_secrets._get_audit_chain", return_value=mock_chain):
            rc, stdout, _stderr = _run_cli(["audit"])

        assert rc == 0
        assert "generate" in stdout
        assert "my-secret" in stdout
        assert "2026-05-01" in stdout
        mock_chain.tail.assert_called_once_with(20)


# ---------------------------------------------------------------------------
# test_cli_use_injects_env_not_stdout
# ---------------------------------------------------------------------------


class TestCliUse:
    def test_cli_use_injects_env_not_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ratis-secret use must NOT print the secret value to stdout."""
        secret_value = "super-secret-do-not-print-me-abc123"

        fake_get_result = {
            "name": "my-api-key",
            "value": secret_value,
            "lease_id": "lease-abc",
            "version": 1,
            "issued_at": "2026-05-01T10:00:00+00:00",
            "expires_at": None,
            "category": "A",
        }

        captured_env: dict[str, str] = {}
        captured_cmd: list[str] = []

        def mock_subprocess_run(cmd: str, *, env: dict, shell: bool, **kwargs: Any) -> Any:
            captured_env.update(env)
            captured_cmd.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("agent_mcp.secrets.cli_secrets.secret_get", return_value=fake_get_result),
            patch("agent_mcp.secrets.cli_secrets.subprocess.run", side_effect=mock_subprocess_run),
        ):
            rc, stdout, stderr = _run_cli(["use", "my-api-key", "--cmd", "echo hello"])

        assert rc == 0
        # The secret value must NOT appear in stdout or stderr.
        assert secret_value not in stdout, "Secret value must not be printed to stdout"
        assert secret_value not in stderr, "Secret value must not be printed to stderr"
        # But it must have been injected into the subprocess env.
        assert "MY_API_KEY" in captured_env
        assert captured_env["MY_API_KEY"] == secret_value

    def test_cli_use_not_found_exits_nonzero(self) -> None:
        """ratis-secret use exits non-zero if the secret is not found."""
        with patch(
            "agent_mcp.secrets.cli_secrets.secret_get",
            return_value={"error": "not_found", "name": "missing"},
        ):
            rc, _stdout, _stderr = _run_cli(["use", "missing", "--cmd", "echo hi"])

        assert rc != 0


# ---------------------------------------------------------------------------
# test_cli_revoke_calls_secret_revoke
# ---------------------------------------------------------------------------


class TestCliRevoke:
    def test_cli_revoke_calls_secret_revoke(self) -> None:
        """ratis-secret revoke <lease_id> delegates to secret_revoke()."""
        mock_revoke = MagicMock(return_value={"lease_id": "lease-abc", "revoked": True, "provider": ""})

        with patch("agent_mcp.secrets.cli_secrets.secret_revoke", mock_revoke):
            rc, stdout, _stderr = _run_cli(["revoke", "lease-abc"])

        assert rc == 0
        mock_revoke.assert_called_once_with("lease-abc")
        assert "lease-abc" in stdout or "revoked" in stdout.lower()

    def test_cli_revoke_not_found_exits_nonzero(self) -> None:
        """ratis-secret revoke exits non-zero when the lease is not found."""
        mock_revoke = MagicMock(return_value={"error": "not_found", "lease_id": "missing"})

        with patch("agent_mcp.secrets.cli_secrets.secret_revoke", mock_revoke):
            rc, _stdout, _stderr = _run_cli(["revoke", "missing"])

        assert rc != 0


# ---------------------------------------------------------------------------
# test_cli_rotate — real implementation (PR 8 replaced PR 7 stub)
# ---------------------------------------------------------------------------


class TestCliRotate:
    def test_cli_rotate_unknown_secret_returns_error(self) -> None:
        """ratis-secret rotate <name> returns exit code 1 when secret not found.

        Previously this test verified a stub message; the stub was replaced by a
        real implementation in PR 8 (format param feature). The new contract is:
        rotating an unknown secret returns exit code 1 with an error message.
        """
        rc, _stdout, stderr = _run_cli(["rotate", "does-not-exist"])
        assert rc == 1
        assert "rotate failed" in stderr or "not_found" in stderr
