"""TDD coverage for agent_mcp.secrets.cli_admin — ratis-admin CLI (Module 10, PR 5).

Strategy
--------
* Mock Keychain to avoid macOS security CLI dependency.
* Mock httpx.post to avoid real network calls.
* Mock webbrowser.open to verify URL without opening a browser.
* Verify security posture: ADMIN_API_KEY is NEVER printed to stdout/stderr.
"""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

from agent_mcp.secrets import cli_admin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    """Run the CLI and capture stdout/stderr. Returns (exit_code, stdout, stderr)."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = cli_admin.main(args)
        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return rc, stdout, stderr


_ADMIN_KEY = "test-admin-key-padded-to-32-chars-min"  # pragma: allowlist secret
_FAKE_OTT = "fake.ott.jwt"
_FAKE_REDIRECT_URL = "http://localhost:8003/?ott=fake.ott.jwt&redirect=/admin/db-approvals"


# ---------------------------------------------------------------------------
# test_cmd_open_posts_to_bootstrap
# ---------------------------------------------------------------------------


class TestCmdOpenPostsToBootstrap:
    def test_posts_to_correct_url(self) -> None:
        """cmd_open POSTs to {host}/admin/session-bootstrap with Authorization header."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": _FAKE_REDIRECT_URL,
        }

        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser"),
        ):
            mock_httpx.post.return_value = fake_response
            rc = cli_admin.cmd_open(path="/admin/db-approvals", host="http://localhost:8003")

        assert rc == 0
        # Verify POST was made to the correct endpoint
        call_args = mock_httpx.post.call_args
        assert "http://localhost:8003/admin/session-bootstrap" in call_args[0]
        # Verify Authorization header carries the admin key
        headers = call_args[1].get("headers") or call_args[0][1] if len(call_args[0]) > 1 else {}
        # headers could be in kwargs
        if not headers and "headers" in call_args[1]:
            headers = call_args[1]["headers"]
        assert "Authorization" in headers or "authorization" in {k.lower() for k in headers}

    def test_authorization_header_value(self) -> None:
        """The Authorization header value is Bearer <admin_key>."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": _FAKE_REDIRECT_URL,
        }

        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser"),
        ):
            mock_httpx.post.return_value = fake_response
            cli_admin.cmd_open(path="/admin/db-approvals", host="http://localhost:8003")

        call_kwargs = mock_httpx.post.call_args[1]
        headers = call_kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {_ADMIN_KEY}"


# ---------------------------------------------------------------------------
# test_cmd_open_opens_browser
# ---------------------------------------------------------------------------


class TestCmdOpenOpensBrowser:
    def test_opens_browser_with_redirect_url(self) -> None:
        """webbrowser.open is called with the redirect_url from the server response."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": _FAKE_REDIRECT_URL,
        }

        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser") as mock_wb,
        ):
            mock_httpx.post.return_value = fake_response
            rc = cli_admin.cmd_open(path="/admin/db-approvals", host="http://localhost:8003")

        assert rc == 0
        mock_wb.open.assert_called_once_with(_FAKE_REDIRECT_URL)

    def test_admin_key_not_in_stdout(self) -> None:
        """The ADMIN_API_KEY value is NEVER printed to stdout or stderr."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": _FAKE_REDIRECT_URL,
        }

        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser"),
        ):
            mock_httpx.post.return_value = fake_response
            _rc, stdout, stderr = _run_cli(["open", "/admin/db-approvals"])

        assert _ADMIN_KEY not in stdout
        assert _ADMIN_KEY not in stderr


# ---------------------------------------------------------------------------
# test_cmd_open_keychain_miss
# ---------------------------------------------------------------------------


class TestCmdOpenKeychainMiss:
    def test_keychain_miss_exits_with_code_1(self) -> None:
        """Exit code 1 when ADMIN_API_KEY is absent from the Keychain."""
        from agent_mcp.errors import KeychainMiss

        fake_kc = MagicMock()
        fake_kc.get.side_effect = KeychainMiss("admin-api-key not found")

        with patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc):
            rc, _stdout, _stderr = _run_cli(["open", "/admin/db-approvals"])

        assert rc == 1

    def test_keychain_miss_prints_helpful_message(self) -> None:
        """A helpful message is printed to stderr when the key is missing."""
        from agent_mcp.errors import KeychainMiss

        fake_kc = MagicMock()
        fake_kc.get.side_effect = KeychainMiss("admin-api-key not found")

        with patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc):
            _rc, _stdout, stderr = _run_cli(["open", "/admin/db-approvals"])

        # stderr should contain guidance (not the raw exception text alone)
        assert "admin" in stderr.lower() or "keychain" in stderr.lower() or "key" in stderr.lower()

    def test_keychain_miss_does_not_crash(self) -> None:
        """KeychainMiss is handled gracefully — no traceback printed."""
        from agent_mcp.errors import KeychainMiss

        fake_kc = MagicMock()
        fake_kc.get.side_effect = KeychainMiss("admin-api-key not found")

        with patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc):
            _rc, stdout, stderr = _run_cli(["open", "/admin/db-approvals"])

        assert "Traceback" not in stderr
        assert "Traceback" not in stdout


# ---------------------------------------------------------------------------
# test_cmd_open_default_service_mapping
# ---------------------------------------------------------------------------


class TestCmdOpenServiceMapping:
    def test_pa_service_maps_to_port_8003(self) -> None:
        """--service pa maps to http://localhost:8003."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": "http://localhost:8003/?ott=fake.ott.jwt&redirect=/admin/x",
        }
        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser"),
        ):
            mock_httpx.post.return_value = fake_response
            rc, _stdout, _stderr = _run_cli(["open", "/admin/x", "--service", "pa"])

        assert rc == 0
        url_called = mock_httpx.post.call_args[0][0]
        assert "8003" in url_called

    def test_rw_service_maps_to_port_8004(self) -> None:
        """--service rw maps to http://localhost:8004."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": "http://localhost:8004/?ott=fake.ott.jwt&redirect=/admin/x",
        }
        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser"),
        ):
            mock_httpx.post.return_value = fake_response
            rc, _stdout, _stderr = _run_cli(["open", "/admin/x", "--service", "rw"])

        assert rc == 0
        url_called = mock_httpx.post.call_args[0][0]
        assert "8004" in url_called

    def test_au_service_maps_to_port_8001(self) -> None:
        """--service au maps to http://localhost:8001."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "ott": _FAKE_OTT,
            "redirect_url": "http://localhost:8001/?ott=fake.ott.jwt&redirect=/admin/x",
        }
        fake_kc = MagicMock()
        fake_kc.get.return_value = _ADMIN_KEY

        with (
            patch("agent_mcp.secrets.cli_admin._get_keychain", return_value=fake_kc),
            patch("agent_mcp.secrets.cli_admin.httpx") as mock_httpx,
            patch("agent_mcp.secrets.cli_admin.webbrowser"),
        ):
            mock_httpx.post.return_value = fake_response
            rc, _stdout, _stderr = _run_cli(["open", "/admin/x", "--service", "au"])

        assert rc == 0
        url_called = mock_httpx.post.call_args[0][0]
        assert "8001" in url_called
