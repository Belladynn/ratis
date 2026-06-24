"""TDD coverage for Module 10 PR 7 — secret_rotate + secret_rollback.

Strategy
--------
* Inject Keychain / SecretMetaDB doubles via set_*() — no real macOS calls.
* Verify rotate creates v+1, old version still accessible during window.
* Verify cleanup_rotation_windows revokes expired windows.
* Verify rollback reactivates a prior version, with missing-Keychain warning.
* Verify audit actions "rotate" and "rollback" are emitted.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_mcp.keychain import Keychain
from agent_mcp.secrets.meta_db import SecretMetaDB
from agent_mcp.tools import secrets_tools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_AUDIT_KEY = "cafebabe" * 8  # 64-hex-char fake signing key


def _make_fake_keychain() -> tuple[Keychain, dict[str, Any]]:
    """Return a Keychain backed by an in-memory fake store."""
    store: dict[str, Any] = {
        "store": {},
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
def _inject_doubles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Inject fake Keychain + real (tmp) MetaDB + fake audit dir for each test."""
    kc, store = _make_fake_keychain()
    store["store"]["ratis-provider-admin/audit-signing"] = _FAKE_AUDIT_KEY

    db = SecretMetaDB(tmp_path / "secrets.db")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("RATIS_SECRETS_AUDIT_DIR", str(audit_dir))

    secrets_tools.set_keychain(kc)
    secrets_tools.set_meta_db(db)
    secrets_tools.set_audit_dir(audit_dir)

    yield

    secrets_tools.set_keychain(None)
    secrets_tools.set_meta_db(None)
    secrets_tools.set_audit_dir(None)


@pytest.fixture
def kc_store(tmp_path: Path) -> dict[str, Any]:
    """Expose the raw fake-keychain store for assertions."""
    # The keychain was injected in _inject_doubles; access via the module.
    kc = secrets_tools._get_keychain()
    return kc._cache  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests — secret_rotate
# ---------------------------------------------------------------------------


class TestSecretRotate:
    def test_rotate_creates_new_version(self) -> None:
        """After rotate, get_active returns v2."""
        secrets_tools.secret_generate(name="s1", description="initial")
        db = secrets_tools._get_meta_db()
        before = db.get_active("s1")
        assert before is not None
        assert before["version"] == 1

        result = secrets_tools.secret_rotate(name="s1")
        assert result["name"] == "s1"
        assert result["old_version"] == 1
        assert result["new_version"] == 2

        after = db.get_active("s1")
        assert after is not None
        assert after["version"] == 2

    def test_rotate_old_version_still_accessible_during_window(self) -> None:
        """Old lease_id is not revoked while window_minutes > 0."""
        secrets_tools.secret_generate(name="s2", description="initial")
        db = secrets_tools._get_meta_db()

        old_active = db.get_active("s2")
        assert old_active is not None
        old_lease_id = old_active["lease_id"]

        secrets_tools.secret_rotate(name="s2", window_minutes=60)

        # Old version row should still have revoked_at = NULL
        row = db.get_by_lease_id(old_lease_id)
        assert row is not None
        assert row["revoked_at"] is None, "Old version must not be revoked during window"

    def test_rotate_returns_no_values(self) -> None:
        """secret_rotate MUST never return the secret value."""
        secrets_tools.secret_generate(name="s3")
        result = secrets_tools.secret_rotate(name="s3")
        assert "value" not in result
        assert "new_value" not in result

    def test_rotate_returns_required_fields(self) -> None:
        """Return dict must contain name, old_version, new_version, new_lease_id, window_expires_at."""
        secrets_tools.secret_generate(name="s4")
        result = secrets_tools.secret_rotate(name="s4")
        assert "name" in result
        assert "old_version" in result
        assert "new_version" in result
        assert "new_lease_id" in result
        assert "window_expires_at" in result

    def test_rotate_emits_audit(self, tmp_path: Path) -> None:
        """Audit chain must contain action='rotate' after rotation."""
        secrets_tools.secret_generate(name="s5")
        secrets_tools.secret_rotate(name="s5")

        audit_dir = tmp_path / "audit"
        files = list(audit_dir.glob("secrets-*.jsonl"))
        assert files, "Expected at least one audit file"
        lines = files[0].read_text().strip().splitlines()
        actions = [json.loads(ln)["action"] for ln in lines]
        assert "rotate" in actions

    def test_rotate_uses_versioned_keychain_key(self) -> None:
        """New version must be stored under secret/{name}/v2 in Keychain."""
        secrets_tools.secret_generate(name="s6")
        secrets_tools.secret_rotate(name="s6")

        kc = secrets_tools._get_keychain()
        # Accessing the internal store of the fake keychain
        # We verify by attempting a get on the versioned key
        from agent_mcp.errors import KeychainMiss

        try:
            val = kc.get("secret/s6/v2")
            assert isinstance(val, str)
            assert len(val) > 0
        except KeychainMiss:
            pytest.fail("secret/s6/v2 should exist in Keychain after rotate")

    def test_rotate_nonexistent_secret_returns_error(self) -> None:
        """Rotating a name with no active version returns an error dict."""
        result = secrets_tools.secret_rotate(name="does-not-exist")
        assert "error" in result

    def test_rotate_sets_rotation_window_on_old_version(self) -> None:
        """Old version's rotation_window_expires_at must be set after rotate."""
        secrets_tools.secret_generate(name="s7")
        db = secrets_tools._get_meta_db()
        old = db.get_active("s7")
        assert old is not None
        old_lease_id = old["lease_id"]

        result = secrets_tools.secret_rotate(name="s7", window_minutes=30)

        # rotation_window_expires_at must be set on old row
        row = db.get_by_lease_id(old_lease_id)
        assert row is not None
        assert row.get("rotation_window_expires_at") is not None
        # And it must roughly match window_expires_at from the result
        assert row["rotation_window_expires_at"] == result["window_expires_at"]


class TestCleanupRotationWindows:
    def test_cleanup_revokes_expired_window(self) -> None:
        """Versions with rotation_window_expires_at < now are revoked by cleanup."""
        secrets_tools.secret_generate(name="s8")
        db = secrets_tools._get_meta_db()

        # Simulate rotation with a window that has already expired
        old = db.get_active("s8")
        assert old is not None
        old_lease_id = old["lease_id"]

        # Set rotation_window_expires_at to the past
        past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
        db.set_rotation_window(old_lease_id, past)

        # Create v2 so old version is "the previous one"
        secrets_tools.secret_rotate(name="s8", window_minutes=60)

        # Now manually reset the new v2's window to not have an expired window
        # and mark old v1 as having expired window by calling set_rotation_window again
        db.set_rotation_window(old_lease_id, past)

        # Run cleanup
        secrets_tools.cleanup_rotation_windows()

        # Old lease_id should now be revoked
        row = db.get_by_lease_id(old_lease_id)
        assert row is not None
        assert row["revoked_at"] is not None, "Expired window version must be revoked by cleanup"

    def test_cleanup_does_not_revoke_active_window(self) -> None:
        """Versions whose window has NOT expired are left untouched."""
        secrets_tools.secret_generate(name="s9")
        db = secrets_tools._get_meta_db()
        old = db.get_active("s9")
        assert old is not None
        old_lease_id = old["lease_id"]

        # Rotate with a future window
        secrets_tools.secret_rotate(name="s9", window_minutes=60)
        # window_expires_at is 60 min from now — should NOT be revoked

        secrets_tools.cleanup_rotation_windows()

        row = db.get_by_lease_id(old_lease_id)
        assert row is not None
        assert row["revoked_at"] is None, "Non-expired window must not be revoked"


# ---------------------------------------------------------------------------
# Tests — secret_get versioned key fallback
# ---------------------------------------------------------------------------


class TestSecretGetVersionedKey:
    def test_secret_get_uses_versioned_keychain_key(self) -> None:
        """After generate + rotate, secret_get reads from secret/{name}/v2."""
        secrets_tools.secret_generate(name="vk1")
        secrets_tools.secret_rotate(name="vk1")

        result = secrets_tools.secret_get("vk1")
        assert result.get("error") is None
        assert "value" in result
        assert result["version"] == 2

    def test_secret_get_falls_back_to_legacy(self) -> None:
        """When secret/{name}/v1 is missing but secret/{name} exists, fallback works."""
        # Simulate PR 1 pattern: store under legacy key (no /v1)
        kc = secrets_tools._get_keychain()
        kc.set("secret/legacy-s", "legacy-value-abc")

        db = secrets_tools._get_meta_db()
        import secrets as _s

        db.insert_version(
            name="legacy-s",
            category="A",
            version=1,
            lease_id=_s.token_urlsafe(24),
            issued_at=datetime.datetime.now(datetime.UTC).isoformat(),
            expires_at=None,
            description="",
        )

        result = secrets_tools.secret_get("legacy-s")
        assert result.get("error") is None, f"Expected no error, got: {result}"
        assert result["value"] == "legacy-value-abc"

    def test_secret_get_versioned_key_preferred_over_legacy(self) -> None:
        """Versioned key secret/{name}/v1 is preferred over legacy secret/{name}."""
        kc = secrets_tools._get_keychain()
        # Both keys present; versioned should win
        kc.set("secret/pref-s/v1", "versioned-value")
        kc.set("secret/pref-s", "legacy-value")

        db = secrets_tools._get_meta_db()
        import secrets as _s

        db.insert_version(
            name="pref-s",
            category="A",
            version=1,
            lease_id=_s.token_urlsafe(24),
            issued_at=datetime.datetime.now(datetime.UTC).isoformat(),
            expires_at=None,
            description="",
        )

        result = secrets_tools.secret_get("pref-s")
        assert result.get("error") is None
        assert result["value"] == "versioned-value"


# ---------------------------------------------------------------------------
# Tests — secret_rollback
# ---------------------------------------------------------------------------


class TestSecretRollback:
    def test_rollback_reactivates_old_version(self) -> None:
        """rotate → rollback → get_active returns v1."""
        secrets_tools.secret_generate(name="rb1")
        db = secrets_tools._get_meta_db()

        v1 = db.get_active("rb1")
        assert v1 is not None
        assert v1["version"] == 1

        secrets_tools.secret_rotate(name="rb1")
        v2 = db.get_active("rb1")
        assert v2 is not None
        assert v2["version"] == 2

        result = secrets_tools.secret_rollback(name="rb1", version=1)
        assert result["name"] == "rb1"
        assert result["rolled_back_to_version"] == 1

        active = db.get_active("rb1")
        assert active is not None
        assert active["version"] == 1

    def test_rollback_revokes_current_version(self) -> None:
        """After rollback, the current version (v2) must be revoked."""
        secrets_tools.secret_generate(name="rb2")
        db = secrets_tools._get_meta_db()

        secrets_tools.secret_rotate(name="rb2")
        v2 = db.get_active("rb2")
        assert v2 is not None
        v2_lease_id = v2["lease_id"]

        secrets_tools.secret_rollback(name="rb2", version=1)

        row = db.get_by_lease_id(v2_lease_id)
        assert row is not None
        assert row["revoked_at"] is not None, "v2 must be revoked after rollback"

    def test_rollback_returns_no_values(self) -> None:
        """secret_rollback MUST never return the secret value."""
        secrets_tools.secret_generate(name="rb3")
        secrets_tools.secret_rotate(name="rb3")
        result = secrets_tools.secret_rollback(name="rb3", version=1)
        assert "value" not in result

    def test_rollback_with_missing_keychain_entry(self) -> None:
        """When Keychain entry for old version is absent, warning is returned."""
        secrets_tools.secret_generate(name="rb4")
        db = secrets_tools._get_meta_db()
        v1_lease = db.get_active("rb4")
        assert v1_lease is not None

        secrets_tools.secret_rotate(name="rb4")

        # Manually delete the v1 Keychain entry to simulate it being purged
        kc = secrets_tools._get_keychain()
        # Try to delete both versioned and legacy keys
        kc.delete("secret/rb4/v1")
        kc.delete("secret/rb4")

        result = secrets_tools.secret_rollback(name="rb4", version=1)
        assert result.get("warning") is not None, "Must return warning when Keychain entry is missing"
        assert "keychain" in result["warning"].lower() or "missing" in result["warning"].lower()

    def test_rollback_nonexistent_version_returns_error(self) -> None:
        """Rolling back to a version that doesn't exist returns error dict."""
        secrets_tools.secret_generate(name="rb5")
        result = secrets_tools.secret_rollback(name="rb5", version=99)
        assert "error" in result

    def test_rollback_emits_audit(self, tmp_path: Path) -> None:
        """Audit chain must contain action='rollback' after rollback."""
        secrets_tools.secret_generate(name="rb6")
        secrets_tools.secret_rotate(name="rb6")
        secrets_tools.secret_rollback(name="rb6", version=1)

        audit_dir = tmp_path / "audit"
        files = list(audit_dir.glob("secrets-*.jsonl"))
        assert files, "Expected at least one audit file"
        lines = files[0].read_text().strip().splitlines()
        actions = [json.loads(ln)["action"] for ln in lines]
        assert "rollback" in actions

    def test_rollback_returns_lease_id(self) -> None:
        """rollback result must contain the reactivated lease_id."""
        secrets_tools.secret_generate(name="rb7")
        db = secrets_tools._get_meta_db()
        v1 = db.get_active("rb7")
        assert v1 is not None
        v1_lease_id = v1["lease_id"]

        secrets_tools.secret_rotate(name="rb7")
        result = secrets_tools.secret_rollback(name="rb7", version=1)
        assert result.get("lease_id") == v1_lease_id


# ---------------------------------------------------------------------------
# Tests — secret_generate versioned storage
# ---------------------------------------------------------------------------


class TestSecretGenerateVersionedStorage:
    def test_generate_v1_stored_under_versioned_key(self) -> None:
        """secret_generate stores under secret/{name}/v1 (new pattern)."""
        secrets_tools.secret_generate(name="gv1")
        kc = secrets_tools._get_keychain()
        from agent_mcp.errors import KeychainMiss

        try:
            val = kc.get("secret/gv1/v1")
            assert isinstance(val, str)
            assert len(val) > 0
        except KeychainMiss:
            pytest.fail("secret/gv1/v1 must exist after generate")

    def test_generate_v1_legacy_fallback_compatible(self) -> None:
        """secret_get still works for v1 stored under legacy key."""
        # Old-style secret (no /vN suffix) — PR1 compat
        kc = secrets_tools._get_keychain()
        kc.set("secret/oldstyle", "old-value-xyz")

        db = secrets_tools._get_meta_db()
        import secrets as _s

        db.insert_version(
            name="oldstyle",
            category="A",
            version=1,
            lease_id=_s.token_urlsafe(24),
            issued_at=datetime.datetime.now(datetime.UTC).isoformat(),
            expires_at=None,
            description="",
        )

        result = secrets_tools.secret_get("oldstyle")
        assert result.get("error") is None
        assert result["value"] == "old-value-xyz"


# ---------------------------------------------------------------------------
# Tests — secret_list after rotate
# ---------------------------------------------------------------------------


class TestSecretListAfterRotate:
    def test_rotate_then_list_shows_one_active(self) -> None:
        """After rotation, secret_list shows only one active version per name."""
        secrets_tools.secret_generate(name="lst1")
        secrets_tools.secret_rotate(name="lst1")

        results = secrets_tools.secret_list()
        lst1_entries = [r for r in results if r["name"] == "lst1"]
        # list_all returns one row per name (latest version)
        assert len(lst1_entries) == 1
        # It should be the latest (v2)
        assert lst1_entries[0]["version"] == 2


# ---------------------------------------------------------------------------
# Tests — secret_rotate with format param (new feature)
# ---------------------------------------------------------------------------


class TestSecretRotateFormat:
    """Test `format` parameter on secret_rotate (new feature)."""

    def test_rotate_with_format_hex_stores_hex_value(self) -> None:
        """secret_rotate with format=hex stores a hex token in Keychain for new version."""
        secrets_tools.secret_generate(name="rot-hex-r")
        result = secrets_tools.secret_rotate(name="rot-hex-r", format="hex")
        assert "error" not in result
        assert "value" not in result
        assert result["new_version"] == 2

        get_result = secrets_tools.secret_get("rot-hex-r")
        assert "error" not in get_result
        value = get_result["value"]
        # Must be valid hex
        int(value, 16)

    def test_rotate_with_format_alphanumeric(self) -> None:
        """secret_rotate with format=alphanumeric stores an alphanumeric token."""
        import string

        secrets_tools.secret_generate(name="rot-alnum-r")
        result = secrets_tools.secret_rotate(name="rot-alnum-r", format="alphanumeric")
        assert "error" not in result

        get_result = secrets_tools.secret_get("rot-alnum-r")
        value = get_result["value"]
        assert all(c in string.ascii_letters + string.digits for c in value)

    def test_rotate_with_format_uuid(self) -> None:
        """secret_rotate with format=uuid stores a UUID value."""
        import re

        secrets_tools.secret_generate(name="rot-uuid-r")
        result = secrets_tools.secret_rotate(name="rot-uuid-r", format="uuid")
        assert "error" not in result

        get_result = secrets_tools.secret_get("rot-uuid-r")
        value = get_result["value"]
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value), (
            f"not a UUID: {value!r}"
        )

    def test_rotate_with_unknown_format_returns_error_dict(self) -> None:
        """Unknown format must return {error, name, detail} — never raise exception."""
        secrets_tools.secret_generate(name="rot-bad-r")
        result = secrets_tools.secret_rotate(name="rot-bad-r", format="binary")
        assert "error" in result
        assert result.get("name") == "rot-bad-r"
        assert "detail" in result

    def test_rotate_default_format_backwards_compat(self) -> None:
        """secret_rotate with no format param still works (urlsafe default, backward compat)."""
        secrets_tools.secret_generate(name="rot-back-compat")
        result = secrets_tools.secret_rotate(name="rot-back-compat")
        assert "error" not in result
        assert result["new_version"] == 2
