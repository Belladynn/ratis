"""TDD coverage for `agent_mcp.secrets.lease` — context manager + secret_renew.

Strategy
--------
* Inject Keychain doubles and real (tmp) SecretMetaDB — no real macOS security calls.
* Test Cat-A path: reads from Keychain, no provision/revoke calls.
* Test Cat-B path: calls provision then revoke on exit.
* Test exception safety: revoke still happens if an exception is raised inside the block.
* Test secret_renew: updates expires_at in SQLite.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agent_mcp.keychain import Keychain
from agent_mcp.secrets.meta_db import SecretMetaDB
from agent_mcp.tools import secrets_tools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_AUDIT_KEY = "cafebabe" * 8  # 64-hex-char fake signing key


def _make_fake_keychain(initial_store: dict[str, str] | None = None) -> tuple[Keychain, dict[str, Any]]:
    """Return a Keychain backed by an in-memory fake store."""
    store: dict[str, Any] = {
        "store": dict(initial_store or {}),
        "calls": [],
    }

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        store["calls"].append(list(argv))
        account = None
        for i, tok in enumerate(argv):
            if tok == "-a" and i + 1 < len(argv):
                account = argv[i + 1]

        if argv[1] == "find-generic-password":
            if account in store["store"]:
                return subprocess.CompletedProcess(argv, 0, store["store"][account] + "\n", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        if argv[1] == "add-generic-password":
            value = kwargs.get("input")
            if not value:
                return subprocess.CompletedProcess(argv, 1, "", "no stdin")
            store["store"][account] = value
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "delete-generic-password":
            if account in store["store"]:
                del store["store"][account]
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        return subprocess.CompletedProcess(argv, 1, "", "unhandled")

    kc = Keychain(runner=runner)
    return kc, store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_secrets_tools_injections() -> Iterator[None]:
    """Reset module-level injection state in secrets_tools between tests."""
    yield
    secrets_tools.set_keychain(None)
    secrets_tools.set_meta_db(None)
    secrets_tools.set_audit_dir(None)
    secrets_tools._reset_for_tests()


@pytest.fixture
def tmp_meta_db(tmp_path: Path) -> SecretMetaDB:
    return SecretMetaDB(tmp_path / "secrets.db")


@pytest.fixture
def fake_kc_with_audit_key() -> tuple[Keychain, dict[str, Any]]:
    """Fake Keychain pre-seeded with the audit signing key."""
    kc, store = _make_fake_keychain({"ratis-provider-admin/audit-signing": _FAKE_AUDIT_KEY})
    return kc, store


@pytest.fixture
def injected_tools(
    tmp_path: Path,
    fake_kc_with_audit_key: tuple[Keychain, dict[str, Any]],
    tmp_meta_db: SecretMetaDB,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Inject fake doubles into secrets_tools and return context."""
    kc, store = fake_kc_with_audit_key
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("RATIS_SECRETS_AUDIT_DIR", str(audit_dir))

    secrets_tools.set_keychain(kc)
    secrets_tools.set_meta_db(tmp_meta_db)
    secrets_tools.set_audit_dir(audit_dir)

    return {"kc": kc, "store": store, "db": tmp_meta_db, "audit_dir": audit_dir}


# ---------------------------------------------------------------------------
# test_secret_with — Cat A (no provider)
# ---------------------------------------------------------------------------


class TestSecretWithCatA:
    def test_secret_with_cat_a_yields_value(self, injected_tools: dict[str, Any]) -> None:
        """Cat-A secret: context manager yields the stored Keychain value."""
        from agent_mcp.secrets.lease import secret_with

        store = injected_tools["store"]["store"]
        # Pre-seed a Cat-A secret in Keychain (account = secret/my-secret).
        store["secret/my-secret"] = "my-secret-value"
        # Also need metadata in DB for the active lookup.
        db: SecretMetaDB = injected_tools["db"]
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-abc",
            issued_at="2026-01-01T00:00:00+00:00",
            expires_at=None,
            description="test",
        )

        with secret_with("my-secret") as val:
            assert val == "my-secret-value"
            assert val != ""

    def test_secret_with_auto_revokes_on_exit(self, injected_tools: dict[str, Any]) -> None:
        """After the context manager exits, the lease is marked revoked in SQLite."""
        from agent_mcp.secrets.lease import secret_with

        store = injected_tools["store"]["store"]
        store["secret/my-secret"] = "my-value"
        db: SecretMetaDB = injected_tools["db"]
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-xyz",
            issued_at="2026-01-01T00:00:00+00:00",
            expires_at=None,
            description="",
        )

        with secret_with("my-secret"):
            # Inside the block, revoked_at should still be None.
            row = db.get_by_lease_id("lease-xyz")
            assert row is not None
            assert row["revoked_at"] is None

        # After the block, revoked_at must be set.
        row = db.get_by_lease_id("lease-xyz")
        assert row is not None
        assert row["revoked_at"] is not None

    def test_secret_with_exception_still_revokes(self, injected_tools: dict[str, Any]) -> None:
        """Exception inside the block must not prevent revocation."""
        from agent_mcp.secrets.lease import secret_with

        store = injected_tools["store"]["store"]
        store["secret/my-secret"] = "val"
        db: SecretMetaDB = injected_tools["db"]
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-err",
            issued_at="2026-01-01T00:00:00+00:00",
            expires_at=None,
            description="",
        )

        with pytest.raises(RuntimeError, match="test-error"), secret_with("my-secret"):
            raise RuntimeError("test-error")

        row = db.get_by_lease_id("lease-err")
        assert row is not None
        assert row["revoked_at"] is not None


# ---------------------------------------------------------------------------
# test_secret_with — Cat B (with provider)
# ---------------------------------------------------------------------------


class TestSecretWithCatB:
    def test_secret_with_cat_b_provisions_and_revokes(
        self,
        injected_tools: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """Cat-B path: provision is called on enter, revoke is called on exit."""
        from agent_mcp.secrets.lease import secret_with
        from agent_mcp.secrets.provision import ProvisionResult

        provision_calls: list[dict] = []
        revoke_calls: list[dict] = []

        mock_provider = MagicMock()
        mock_provider.provision.side_effect = lambda admin_token, ttl_seconds: (
            provision_calls.append({"ttl": ttl_seconds})
            or ProvisionResult(
                value="jit-token-value",
                token_id="tok-123",
                expires_at="2026-06-01T00:30:00+00:00",
                metadata={},
            )
        )
        mock_provider.revoke.side_effect = lambda admin_token, token_id: (
            revoke_calls.append({"token_id": token_id}) or True
        )

        # get_provider is patched at the lease module import path.
        # The fake keychain is already injected via injected_tools fixture into
        # secrets_tools._keychain, so no additional patching is needed for Keychain.
        with patch("agent_mcp.secrets.lease.get_provider", return_value=mock_provider):
            with secret_with("jit-secret", provider="github-app", ttl="30m") as val:
                assert val == "jit-token-value"

        assert len(provision_calls) == 1
        assert provision_calls[0]["ttl"] == 1800  # 30m = 1800s
        assert len(revoke_calls) == 1
        assert revoke_calls[0]["token_id"] == "tok-123"


# ---------------------------------------------------------------------------
# test_secret_renew
# ---------------------------------------------------------------------------


class TestSecretRenew:
    def test_secret_renew_updates_expires_at(self, injected_tools: dict[str, Any]) -> None:
        """secret_renew updates expires_at in SQLite for a known lease."""
        from agent_mcp.tools.secrets_tools import secret_renew

        db: SecretMetaDB = injected_tools["db"]
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-renew",
            issued_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:05:00+00:00",
            description="",
        )

        result = secret_renew("lease-renew", extend_ttl="30m")

        assert result["renewed"] is True
        assert result["lease_id"] == "lease-renew"
        assert result["new_expires_at"] is not None

        # Verify the DB was updated.
        row = db.get_by_lease_id("lease-renew")
        assert row is not None
        assert row["expires_at"] == result["new_expires_at"]

    def test_secret_renew_unknown_lease_returns_not_renewed(self, injected_tools: dict[str, Any]) -> None:
        """secret_renew returns {renewed: false} for an unknown lease_id."""
        from agent_mcp.tools.secrets_tools import secret_renew

        result = secret_renew("nonexistent-lease-id", extend_ttl="30m")

        assert result["renewed"] is False
        assert "lease_id" in result
