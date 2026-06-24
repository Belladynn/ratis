"""TDD coverage for `agent_mcp.secrets.inject` — 4 injection adapters.

Each adapter is tested in isolation via dependency injection:
- env-file: writes/replaces lines in a chmod-600 env file
- gh-actions: invokes gh CLI with value via stdin (never argv)
- n8n-env: POSTs to n8n REST API using Keychain-backed token
- docker-compose-env: same logic as env-file but at a custom path

Strategy
--------
* Adapters receive all external dependencies (path, runner, http-client) as
  explicit parameters — no global state. Tests substitute fakes.
* The _inject_gh_actions runner signature mirrors subprocess.run.
* The _inject_n8n function is tested via httpx mock (patch httpx.post).
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_mcp.secrets.inject import (
    _inject_docker_compose_env,
    _inject_env_file,
    _inject_gh_actions,
    _inject_n8n,
)

# ---------------------------------------------------------------------------
# env-file adapter
# ---------------------------------------------------------------------------


class TestInjectEnvFile:
    def test_creates_file_when_absent(self, tmp_path: Path) -> None:
        env_file = tmp_path / "secrets.runtime.env"
        result = _inject_env_file("MY_SECRET", "s3cr3t", env_file)
        assert result == "ok"
        assert env_file.exists()
        content = env_file.read_text()
        assert "MY_SECRET=s3cr3t\n" in content

    def test_file_has_chmod_600(self, tmp_path: Path) -> None:
        env_file = tmp_path / "secrets.runtime.env"
        _inject_env_file("MY_SECRET", "s3cr3t", env_file)
        mode = stat.S_IMODE(env_file.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_updates_existing_line(self, tmp_path: Path) -> None:
        env_file = tmp_path / "env"
        env_file.write_text("OTHER=foo\nMY_SECRET=old_val\nANOTHER=bar\n")
        result = _inject_env_file("MY_SECRET", "new_val", env_file)
        assert result == "ok"
        content = env_file.read_text()
        assert "MY_SECRET=old_val" not in content
        assert "MY_SECRET=new_val" in content
        # Other lines preserved
        assert "OTHER=foo" in content
        assert "ANOTHER=bar" in content

    def test_no_duplicates_on_double_inject(self, tmp_path: Path) -> None:
        env_file = tmp_path / "env"
        _inject_env_file("MY_SECRET", "val1", env_file)
        _inject_env_file("MY_SECRET", "val2", env_file)
        content = env_file.read_text()
        # Only one line for MY_SECRET
        lines_for_key = [line for line in content.splitlines() if line.startswith("MY_SECRET=")]
        assert len(lines_for_key) == 1
        assert lines_for_key[0] == "MY_SECRET=val2"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        env_file = tmp_path / "subdir" / "nested" / "secrets.env"
        result = _inject_env_file("KEY", "value", env_file)
        assert result == "ok"
        assert env_file.exists()


# ---------------------------------------------------------------------------
# gh-actions adapter
# ---------------------------------------------------------------------------


def _make_fake_runner(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    """Return a fake subprocess runner (mimics subprocess.run signature)."""

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return runner


def _make_recording_runner(returncode: int = 0) -> tuple[Any, list[dict[str, Any]]]:
    """Return a runner that records all calls alongside kwargs."""
    calls: list[dict[str, Any]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), "kwargs": kwargs})
        return subprocess.CompletedProcess(argv, returncode, "", "")

    return runner, calls


class TestInjectGhActions:
    def test_returns_ok_on_success(self) -> None:
        runner = _make_fake_runner(returncode=0)
        result = _inject_gh_actions("MY_GH_SECRET", "supersecret", runner=runner)
        assert result == "ok"

    def test_returns_error_on_failure(self) -> None:
        runner = _make_fake_runner(returncode=1, stderr="authentication required")
        result = _inject_gh_actions("MY_GH_SECRET", "supersecret", runner=runner)
        assert result.startswith("error:")
        assert "authentication required" in result

    def test_uses_stdin_not_argv(self) -> None:
        """The secret value MUST NOT appear in argv — only via stdin (input= kwarg)."""
        runner, calls = _make_recording_runner(returncode=0)
        _inject_gh_actions("MY_GH_SECRET", "supersecret_value", runner=runner)
        assert len(calls) == 1
        call = calls[0]
        # Secret must NOT be in argv
        assert "supersecret_value" not in call["argv"], "Secret value leaked into argv — would be visible in `ps aux`"
        # Secret MUST be in the `input` kwarg (stdin)
        assert call["kwargs"].get("input") == "supersecret_value", (
            "Secret value must be passed via stdin (input= kwarg)"
        )

    def test_uses_correct_secret_name(self) -> None:
        runner, calls = _make_recording_runner(returncode=0)
        _inject_gh_actions("DEPLOY_KEY", "value", runner=runner)
        argv = calls[0]["argv"]
        assert "gh" in argv
        assert "secret" in argv
        assert "set" in argv
        assert "DEPLOY_KEY" in argv


# ---------------------------------------------------------------------------
# n8n adapter
# ---------------------------------------------------------------------------


class TestInjectN8n:
    def test_returns_ok_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_post = MagicMock(return_value=mock_response)
        monkeypatch.setattr("httpx.post", mock_post)

        result = _inject_n8n("MY_VAR", "my_value", n8n_api_key="test-api-key")
        assert result == "ok"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        # Verify correct payload shape
        assert call_kwargs.kwargs.get("json") == {"key": "MY_VAR", "value": "my_value"}

    def test_returns_error_when_api_key_missing(self) -> None:
        result = _inject_n8n("MY_VAR", "my_value", n8n_api_key=None)
        assert result == "error: n8n_api_key_missing"

    def test_returns_error_on_http_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        mock_post = MagicMock(return_value=mock_response)
        monkeypatch.setattr("httpx.post", mock_post)

        result = _inject_n8n("MY_VAR", "my_value", n8n_api_key="bad-key")
        assert result.startswith("error:")

    def test_sends_correct_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_post = MagicMock(return_value=mock_response)
        monkeypatch.setattr("httpx.post", mock_post)

        _inject_n8n("MY_VAR", "val", n8n_api_key="my-api-key-123")
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("X-N8N-API-KEY") == "my-api-key-123"

    def test_uses_correct_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_post = MagicMock(return_value=mock_response)
        monkeypatch.setattr("httpx.post", mock_post)

        _inject_n8n("MY_VAR", "val", n8n_api_key="key", n8n_base_url="http://localhost:9999")
        url = mock_post.call_args.args[0]
        assert "9999" in url
        assert "/api/v1/variables" in url


# ---------------------------------------------------------------------------
# docker-compose-env adapter
# ---------------------------------------------------------------------------


class TestInjectDockerComposeEnv:
    def test_creates_env_file_at_custom_path(self, tmp_path: Path) -> None:
        env_file = tmp_path / "myproject" / ".env"
        result = _inject_docker_compose_env("DB_PASS", "secret123", env_file)
        assert result == "ok"
        assert env_file.exists()
        assert "DB_PASS=secret123" in env_file.read_text()

    def test_updates_existing_line(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("DB_PASS=old\nOTHER=keep\n")
        _inject_docker_compose_env("DB_PASS", "new_pass", env_file)
        content = env_file.read_text()
        assert "DB_PASS=old" not in content
        assert "DB_PASS=new_pass" in content
        assert "OTHER=keep" in content

    def test_chmod_600(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        _inject_docker_compose_env("KEY", "val", env_file)
        mode = stat.S_IMODE(env_file.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
