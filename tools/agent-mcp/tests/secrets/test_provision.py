"""TDD coverage for `agent_mcp.secrets.provision`.

Strategy
--------
* All HTTP calls are mocked via `unittest.mock.patch` — no real API calls in CI.
* ``RATIS_SECRETS_E2E=1`` gates real-API tests, skipped by default.
* Each provider tests: provision_ok, revoke_ok, provision_admin_key_missing.
* Utility tests: parse_ttl variants, get_provider_known/unknown.
* Cleanup: test_cleanup_expired_leases via SQLite insert + mock revoke.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agent_mcp.errors import KeychainMiss, ProviderError
from agent_mcp.keychain import Keychain
from agent_mcp.secrets.meta_db import SecretMetaDB
from agent_mcp.secrets.provision import (
    CloudflareR2Provider,
    EASProvider,
    GitHubAppProvider,
    ProvisionResult,
    SentryProvider,
    StripeRestrictedProvider,
    VercelProvider,
    cleanup_expired_leases,
    get_provider,
    parse_ttl,
    set_admin_keychain,
    set_cleanup_done,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_keychain(store: dict[str, str] | None = None) -> Keychain:
    """Return a Keychain backed by an in-memory fake store."""
    _store: dict[str, Any] = {"store": store or {}, "calls": []}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        _store["calls"].append(list(argv))
        account = None
        for i, tok in enumerate(argv):
            if tok == "-a" and i + 1 < len(argv):
                account = argv[i + 1]

        if argv[1] == "find-generic-password":
            if account in _store["store"]:
                return subprocess.CompletedProcess(argv, 0, _store["store"][account] + "\n", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        if argv[1] == "add-generic-password":
            value = kwargs.get("input")
            if not value:
                return subprocess.CompletedProcess(argv, 1, "", "no stdin")
            _store["store"][account] = value
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "delete-generic-password":
            if account in _store["store"]:
                del _store["store"][account]
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        return subprocess.CompletedProcess(argv, 1, "", "unhandled")

    return Keychain(runner=runner)


@pytest.fixture(autouse=True)
def _reset_admin_keychain() -> None:
    """Reset admin keychain injection between tests."""
    set_admin_keychain(None)
    set_cleanup_done(False)
    yield
    set_admin_keychain(None)
    set_cleanup_done(False)


# ---------------------------------------------------------------------------
# parse_ttl
# ---------------------------------------------------------------------------


class TestParseTtl:
    def test_parse_ttl_minutes(self) -> None:
        assert parse_ttl("30m") == 1800

    def test_parse_ttl_hours(self) -> None:
        assert parse_ttl("1h") == 3600

    def test_parse_ttl_days(self) -> None:
        assert parse_ttl("7d") == 604800

    def test_parse_ttl_raw_seconds(self) -> None:
        assert parse_ttl("90") == 90

    def test_parse_ttl_invalid(self) -> None:
        with pytest.raises(ValueError, match="invalid ttl"):
            parse_ttl("abc")

    def test_parse_ttl_48h(self) -> None:
        assert parse_ttl("48h") == 172800

    def test_parse_ttl_integer_zero(self) -> None:
        """Zero seconds is technically valid (rent-and-revoke pattern)."""
        assert parse_ttl("0") == 0


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------


class TestGetProvider:
    def test_get_provider_known_github(self) -> None:
        p = get_provider("github-app")
        assert isinstance(p, GitHubAppProvider)

    def test_get_provider_known_cloudflare(self) -> None:
        p = get_provider("cloudflare-r2")
        assert isinstance(p, CloudflareR2Provider)

    def test_get_provider_known_sentry(self) -> None:
        p = get_provider("sentry")
        assert isinstance(p, SentryProvider)

    def test_get_provider_known_eas(self) -> None:
        p = get_provider("eas")
        assert isinstance(p, EASProvider)

    def test_get_provider_known_vercel(self) -> None:
        p = get_provider("vercel")
        assert isinstance(p, VercelProvider)

    def test_get_provider_known_stripe(self) -> None:
        p = get_provider("stripe-restricted")
        assert isinstance(p, StripeRestrictedProvider)

    def test_get_provider_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            get_provider("imaginary-provider")


# ---------------------------------------------------------------------------
# GitHubAppProvider
# ---------------------------------------------------------------------------


class TestGitHubAppProvider:
    def test_github_provision_ok(self) -> None:
        """Mock subprocess.run for gh CLI — returns a ProvisionResult."""
        admin_kc = _make_fake_keychain({"github": "ghp_admin_token"})
        set_admin_keychain(admin_kc)

        provider = GitHubAppProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"id": 123, "token": "ghp_fresh_token_abc"}'
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = provider.provision("ghp_admin_token", ttl_seconds=3600)

        assert isinstance(result, ProvisionResult)
        assert result.value == "ghp_fresh_token_abc"
        assert result.token_id == "123"

    def test_github_revoke_ok(self) -> None:
        """Mock subprocess.run for gh CLI DELETE — returns True."""
        provider = GitHubAppProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            ok = provider.revoke("ghp_admin_token", "123")

        assert ok is True

    def test_github_provision_admin_key_missing(self) -> None:
        """KeychainMiss from admin keychain → ProviderError raised."""
        admin_kc = _make_fake_keychain({})  # empty — no github key
        set_admin_keychain(admin_kc)

        provider = GitHubAppProvider()
        with pytest.raises((KeychainMiss, ProviderError)):
            provider.provision("", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# CloudflareR2Provider
# ---------------------------------------------------------------------------


class TestCloudflareR2Provider:
    def test_cloudflare_provision_ok(self) -> None:
        admin_kc = _make_fake_keychain(
            {
                "cloudflare": "cf_admin_api_key",
                "cloudflare-account-id": "acc_12345",
            }
        )
        set_admin_keychain(admin_kc)

        provider = CloudflareR2Provider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "result": {"id": "tok_cfr2_001", "value": "cf_r2_scoped_token"},
        }

        with patch("httpx.post", return_value=mock_resp):
            result = provider.provision("cf_admin_api_key", ttl_seconds=3600)

        assert isinstance(result, ProvisionResult)
        assert result.value == "cf_r2_scoped_token"
        assert result.token_id == "tok_cfr2_001"

    def test_cloudflare_revoke_ok(self) -> None:
        admin_kc = _make_fake_keychain(
            {
                "cloudflare": "cf_admin_api_key",
                "cloudflare-account-id": "acc_12345",
            }
        )
        set_admin_keychain(admin_kc)

        provider = CloudflareR2Provider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "result": None}

        with patch("httpx.delete", return_value=mock_resp):
            ok = provider.revoke("cf_admin_api_key", "tok_cfr2_001")

        assert ok is True

    def test_cloudflare_provision_admin_key_missing(self) -> None:
        admin_kc = _make_fake_keychain({})  # no cloudflare key
        set_admin_keychain(admin_kc)

        provider = CloudflareR2Provider()
        with pytest.raises((KeychainMiss, ProviderError)):
            provider.provision("", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# SentryProvider
# ---------------------------------------------------------------------------


class TestSentryProvider:
    def test_sentry_provision_ok(self) -> None:
        admin_kc = _make_fake_keychain(
            {
                "sentry-admin": "sentry_admin_token_xyz",
                "sentry-org": "ratis-hq",
            }
        )
        set_admin_keychain(admin_kc)

        provider = SentryProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "sentry_tok_001", "token": "sntrys_fresh_token"}

        with patch("httpx.post", return_value=mock_resp):
            result = provider.provision("sentry_admin_token_xyz", ttl_seconds=3600)

        assert isinstance(result, ProvisionResult)
        assert result.value == "sntrys_fresh_token"
        assert result.token_id == "sentry_tok_001"

    def test_sentry_revoke_ok(self) -> None:
        admin_kc = _make_fake_keychain(
            {
                "sentry-admin": "sentry_admin_token_xyz",
                "sentry-org": "ratis-hq",
            }
        )
        set_admin_keychain(admin_kc)

        provider = SentryProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.json.return_value = {}

        with patch("httpx.delete", return_value=mock_resp):
            ok = provider.revoke("sentry_admin_token_xyz", "sentry_tok_001")

        assert ok is True

    def test_sentry_provision_admin_key_missing(self) -> None:
        admin_kc = _make_fake_keychain({})  # no sentry-admin key
        set_admin_keychain(admin_kc)

        provider = SentryProvider()
        with pytest.raises((KeychainMiss, ProviderError)):
            provider.provision("", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# EASProvider
# ---------------------------------------------------------------------------


class TestEASProvider:
    def test_eas_provision_ok(self) -> None:
        admin_kc = _make_fake_keychain({"eas": "eas_admin_token_abc"})
        set_admin_keychain(admin_kc)

        provider = EASProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "createAccessToken": {
                    "accessToken": {
                        "id": "eas_tok_001",
                        "token": "eas_fresh_access_token",
                    }
                }
            }
        }

        with patch("httpx.post", return_value=mock_resp):
            result = provider.provision("eas_admin_token_abc", ttl_seconds=3600)

        assert isinstance(result, ProvisionResult)
        assert result.value == "eas_fresh_access_token"
        assert result.token_id == "eas_tok_001"

    def test_eas_revoke_ok(self) -> None:
        admin_kc = _make_fake_keychain({"eas": "eas_admin_token_abc"})
        set_admin_keychain(admin_kc)

        provider = EASProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"deleteAccessToken": {"id": "eas_tok_001"}}}

        with patch("httpx.post", return_value=mock_resp):
            ok = provider.revoke("eas_admin_token_abc", "eas_tok_001")

        assert ok is True

    def test_eas_provision_admin_key_missing(self) -> None:
        admin_kc = _make_fake_keychain({})  # no eas key
        set_admin_keychain(admin_kc)

        provider = EASProvider()
        with pytest.raises((KeychainMiss, ProviderError)):
            provider.provision("", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# VercelProvider
# ---------------------------------------------------------------------------


class TestVercelProvider:
    def test_vercel_provision_ok(self) -> None:
        admin_kc = _make_fake_keychain({"vercel": "vercel_admin_bearer"})
        set_admin_keychain(admin_kc)

        provider = VercelProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "vercel_tok_001",
            "token": "vercel_fresh_token_xyz",
        }

        with patch("httpx.post", return_value=mock_resp):
            result = provider.provision("vercel_admin_bearer", ttl_seconds=3600)

        assert isinstance(result, ProvisionResult)
        assert result.value == "vercel_fresh_token_xyz"
        assert result.token_id == "vercel_tok_001"

    def test_vercel_revoke_ok(self) -> None:
        admin_kc = _make_fake_keychain({"vercel": "vercel_admin_bearer"})
        set_admin_keychain(admin_kc)

        provider = VercelProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "vercel_tok_001"}

        with patch("httpx.delete", return_value=mock_resp):
            ok = provider.revoke("vercel_admin_bearer", "vercel_tok_001")

        assert ok is True

    def test_vercel_provision_admin_key_missing(self) -> None:
        admin_kc = _make_fake_keychain({})  # no vercel key
        set_admin_keychain(admin_kc)

        provider = VercelProvider()
        with pytest.raises((KeychainMiss, ProviderError)):
            provider.provision("", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# StripeRestrictedProvider
# ---------------------------------------------------------------------------


class TestStripeRestrictedProvider:
    def test_stripe_provision_ok(self) -> None:
        admin_kc = _make_fake_keychain({"stripe": "sk_live_admin_key"})
        set_admin_keychain(admin_kc)

        provider = StripeRestrictedProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "rk_stripe_tok_001",
            "key": "rk_live_fresh_restricted_key",
        }

        with patch("httpx.post", return_value=mock_resp):
            result = provider.provision("sk_live_admin_key", ttl_seconds=3600)

        assert isinstance(result, ProvisionResult)
        assert result.value == "rk_live_fresh_restricted_key"
        assert result.token_id == "rk_stripe_tok_001"

    def test_stripe_revoke_ok(self) -> None:
        admin_kc = _make_fake_keychain({"stripe": "sk_live_admin_key"})
        set_admin_keychain(admin_kc)

        provider = StripeRestrictedProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"deleted": True, "id": "rk_stripe_tok_001"}

        with patch("httpx.delete", return_value=mock_resp):
            ok = provider.revoke("sk_live_admin_key", "rk_stripe_tok_001")

        assert ok is True

    def test_stripe_provision_admin_key_missing(self) -> None:
        admin_kc = _make_fake_keychain({})  # no stripe key
        set_admin_keychain(admin_kc)

        provider = StripeRestrictedProvider()
        with pytest.raises((KeychainMiss, ProviderError)):
            provider.provision("", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# cleanup_expired_leases
# ---------------------------------------------------------------------------


class TestCleanupExpiredLeases:
    def test_cleanup_expired_leases_marks_revoked(self, tmp_path: Path) -> None:
        """An expired Cat-B lease has revoked_at set after cleanup_expired_leases()."""
        db = SecretMetaDB(tmp_path / "secrets.db")

        # Insert an expired Cat-B lease (no provider column — base schema)
        expired_at = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        db.insert_version(
            name="expired-sentry-token",
            category="B",
            version=1,
            lease_id="lease_expired_001",
            issued_at=(datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat(),
            expires_at=expired_at,
            description="test expired lease",
        )

        # Verify the lease is initially active
        row = db.get_active("expired-sentry-token")
        assert row is not None

        admin_kc = _make_fake_keychain(
            {
                "sentry-admin": "sentry_admin_token",
                "sentry-org": "ratis-hq",
            }
        )
        set_admin_keychain(admin_kc)

        # Mock revoke to succeed without real HTTP
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.json.return_value = {}

        with patch("httpx.delete", return_value=mock_resp):
            cleanup_expired_leases(db=db)

        # The lease should now be revoked (revoked_at set)
        updated = db._conn.execute(
            "SELECT revoked_at FROM secret_versions WHERE lease_id = ?",
            ("lease_expired_001",),
        ).fetchone()
        assert updated is not None
        assert updated[0] is not None, "revoked_at must be set after cleanup_expired_leases"

    def test_cleanup_expired_leases_no_db(self) -> None:
        """Calling cleanup_expired_leases(db=None) is a no-op (never crashes)."""
        cleanup_expired_leases(db=None)  # must not raise

    def test_cleanup_expired_leases_skips_active(self, tmp_path: Path) -> None:
        """Active Cat-B leases (not yet expired) are NOT touched."""
        db = SecretMetaDB(tmp_path / "secrets.db")

        future_at = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)).isoformat()
        db.insert_version(
            name="active-token",
            category="B",
            version=1,
            lease_id="lease_active_001",
            issued_at=datetime.datetime.now(datetime.UTC).isoformat(),
            expires_at=future_at,
            description="active lease",
        )

        cleanup_expired_leases(db=db)

        # Still active
        row = db.get_active("active-token")
        assert row is not None, "active lease should not be touched by cleanup"
