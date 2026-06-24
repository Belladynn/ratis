"""TDD tests for admin token expiry tracking (Module 10 PR 6).

Covers:
- SecretMetaDB.admin_token_expiry table CRUD
- check_expiry.py script (dry-run, alert, no-expiry, skip-recently-alerted)
- secret_audit_expiry MCP tool
"""

from __future__ import annotations

import datetime
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from agent_mcp.secrets.meta_db import SecretMetaDB

# ---------------------------------------------------------------------------
# SecretMetaDB — admin_token_expiry table
# ---------------------------------------------------------------------------


class TestAdminTokenExpiry:
    def test_upsert_and_get_expiring_soon(self, tmp_path: Path) -> None:
        """Token expiring in 30 days must appear in get_expiring_soon(60)."""
        db = SecretMetaDB(tmp_path / "test.db")
        soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.upsert_admin_expiry(provider="stripe", expires_at=soon, notes="test")
        rows = db.get_expiring_soon(days=60)
        assert any(r["provider"] == "stripe" for r in rows)

    def test_get_expiring_soon_filters_far_future(self, tmp_path: Path) -> None:
        """Token expiring in 90 days must NOT appear in get_expiring_soon(60)."""
        db = SecretMetaDB(tmp_path / "test.db")
        far = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=90)).isoformat()
        db.upsert_admin_expiry(provider="cloudflare", expires_at=far)
        rows = db.get_expiring_soon(days=60)
        assert not any(r["provider"] == "cloudflare" for r in rows)

    def test_mark_alerted(self, tmp_path: Path) -> None:
        """After mark_alerted(), last_alerted_at must be set in the row."""
        db = SecretMetaDB(tmp_path / "test.db")
        soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)).isoformat()
        db.upsert_admin_expiry(provider="sentry", expires_at=soon)
        now_str = datetime.datetime.now(datetime.UTC).isoformat()
        db.mark_alerted(provider="sentry", alerted_at=now_str)
        rows = db.list_admin_expiry()
        row = next(r for r in rows if r["provider"] == "sentry")
        assert row["last_alerted_at"] is not None
        assert row["last_alerted_at"] == now_str

    def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        """Calling upsert twice for the same provider replaces the row."""
        db = SecretMetaDB(tmp_path / "test.db")
        db.upsert_admin_expiry(provider="eas", expires_at=None, notes="first")
        db.upsert_admin_expiry(provider="eas", expires_at=None, notes="second")
        rows = db.list_admin_expiry()
        eas_rows = [r for r in rows if r["provider"] == "eas"]
        assert len(eas_rows) == 1
        assert eas_rows[0]["notes"] == "second"

    def test_get_expiring_soon_null_expires_at(self, tmp_path: Path) -> None:
        """Token with NULL expires_at must NOT appear in expiring_soon."""
        db = SecretMetaDB(tmp_path / "test.db")
        db.upsert_admin_expiry(provider="unknown-token", expires_at=None)
        rows = db.get_expiring_soon(days=60)
        assert not any(r["provider"] == "unknown-token" for r in rows)


# ---------------------------------------------------------------------------
# check_expiry.py script
# ---------------------------------------------------------------------------


def _load_check_expiry():
    """Import check_expiry from the scripts directory as a module."""
    script_path = Path(__file__).parent.parent.parent / "scripts" / "check_expiry.py"
    spec = importlib.util.spec_from_file_location("check_expiry", script_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestCheckExpiryScript:
    def test_check_expiry_script_dry_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DRY_RUN=1 must not POST to n8n even if tokens are expiring."""
        db = SecretMetaDB(tmp_path / "expiry.db")
        soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)).isoformat()
        db.upsert_admin_expiry(provider="stripe", expires_at=soon)

        monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "expiry.db"))
        monkeypatch.setenv("SECRETS_EXPIRY_DRY_RUN", "1")
        monkeypatch.setenv("SECRETS_EXPIRY_N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")

        with patch("httpx.post") as mock_post:
            mod = _load_check_expiry()
            exit_code = mod.run()

        mock_post.assert_not_called()
        assert exit_code == 0

    def test_check_expiry_script_sends_alert(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alert is sent with correct payload when token is expiring soon."""
        db = SecretMetaDB(tmp_path / "expiry.db")
        soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=15)).isoformat()
        db.upsert_admin_expiry(provider="cloudflare", expires_at=soon)

        monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "expiry.db"))
        monkeypatch.setenv("SECRETS_EXPIRY_DRY_RUN", "0")
        monkeypatch.setenv("SECRETS_EXPIRY_N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            mod = _load_check_expiry()
            exit_code = mod.run()

        assert exit_code == 0
        assert mock_post.called
        call_kwargs = mock_post.call_args
        # Payload verification
        payload = call_kwargs[1].get("json") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None)
        assert payload is not None
        assert payload["type"] == "admin_token_expiry"
        assert payload["provider"] == "cloudflare"
        assert "expires_at" in payload
        assert "days_remaining" in payload
        assert "hostname" in payload

    def test_check_expiry_script_no_expiring(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No POST when no tokens are expiring soon."""
        db = SecretMetaDB(tmp_path / "expiry.db")
        far = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=200)).isoformat()
        db.upsert_admin_expiry(provider="stripe", expires_at=far)

        monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "expiry.db"))
        monkeypatch.setenv("SECRETS_EXPIRY_DRY_RUN", "0")
        monkeypatch.setenv("SECRETS_EXPIRY_N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")

        with patch("httpx.post") as mock_post:
            mod = _load_check_expiry()
            exit_code = mod.run()

        mock_post.assert_not_called()
        assert exit_code == 0

    def test_check_expiry_skips_recently_alerted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token alerted < 1 day ago must not trigger another POST."""
        db = SecretMetaDB(tmp_path / "expiry.db")
        soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)).isoformat()
        # alerted 30 minutes ago
        recent_alert = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)).isoformat()
        db.upsert_admin_expiry(provider="sentry", expires_at=soon)
        db.mark_alerted(provider="sentry", alerted_at=recent_alert)

        monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "expiry.db"))
        monkeypatch.setenv("SECRETS_EXPIRY_DRY_RUN", "0")
        monkeypatch.setenv("SECRETS_EXPIRY_N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/test")

        with patch("httpx.post") as mock_post:
            mod = _load_check_expiry()
            exit_code = mod.run()

        mock_post.assert_not_called()
        assert exit_code == 0


# ---------------------------------------------------------------------------
# secret_audit_expiry MCP tool
# ---------------------------------------------------------------------------


class TestSecretAuditExpiryTool:
    def test_secret_audit_expiry_tool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """secret_audit_expiry returns correct structure with expiring_soon and all."""
        # Set up a DB with two entries: one expiring soon, one far future
        db = SecretMetaDB(tmp_path / "expiry.db")
        soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=20)).isoformat()
        far = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=200)).isoformat()
        db.upsert_admin_expiry(provider="github", expires_at=soon, notes="expiring")
        db.upsert_admin_expiry(provider="cloudflare", expires_at=far, notes="fine")

        monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "expiry.db"))

        from agent_mcp.tools import secrets_tools

        secrets_tools.set_meta_db(db)

        try:
            result = secrets_tools.secret_audit_expiry(threshold_days=60)
        finally:
            secrets_tools.set_meta_db(None)

        assert result["threshold_days"] == 60
        assert isinstance(result["expiring_soon"], list)
        assert isinstance(result["all"], list)

        providers_soon = {r["provider"] for r in result["expiring_soon"]}
        assert "github" in providers_soon
        assert "cloudflare" not in providers_soon

        providers_all = {r["provider"] for r in result["all"]}
        assert "github" in providers_all
        assert "cloudflare" in providers_all

        # days_remaining present in expiring_soon
        for entry in result["expiring_soon"]:
            assert "days_remaining" in entry
            assert entry["days_remaining"] >= 0
