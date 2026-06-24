"""TDD coverage for `agent_mcp.secrets.meta_db`.

All tests use `tmp_path` to avoid touching any real DB file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from agent_mcp.secrets.meta_db import SecretMetaDB


@pytest.fixture
def db(tmp_path: Path) -> SecretMetaDB:
    """Fresh in-memory SecretMetaDB for each test."""
    return SecretMetaDB(tmp_path / "test_secrets.db")


class TestInsertAndGetActive:
    def test_insert_returns_id(self, db: SecretMetaDB) -> None:
        row_id = db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-001",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="test",
        )
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_get_active_returns_matching_row(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-001",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="test",
        )
        result = db.get_active("my-secret")
        assert result is not None
        assert result["name"] == "my-secret"
        assert result["lease_id"] == "lease-001"
        assert result["version"] == 1
        assert result["revoked_at"] is None

    def test_get_active_returns_none_for_unknown(self, db: SecretMetaDB) -> None:
        assert db.get_active("nonexistent") is None

    def test_get_active_never_returns_revoked(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-revoked",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        db.revoke("lease-revoked", "2026-01-02T00:00:00Z")
        assert db.get_active("my-secret") is None

    def test_get_active_returns_most_recent_non_revoked(self, db: SecretMetaDB) -> None:
        """When multiple versions exist, get_active returns the latest active one."""
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-old",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        db.revoke("lease-old", "2026-01-02T00:00:00Z")
        db.insert_version(
            name="my-secret",
            category="A",
            version=2,
            lease_id="lease-new",
            issued_at="2026-01-02T00:00:00Z",
            expires_at=None,
            description="",
        )
        result = db.get_active("my-secret")
        assert result is not None
        assert result["lease_id"] == "lease-new"
        assert result["version"] == 2


class TestListAll:
    def test_list_all_returns_all_distinct_names(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="secret-a",
            category="A",
            version=1,
            lease_id="lease-a",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        db.insert_version(
            name="secret-b",
            category="B",
            version=1,
            lease_id="lease-b",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        results = db.list_all()
        names = {r["name"] for r in results}
        assert names == {"secret-a", "secret-b"}

    def test_list_all_never_returns_value_field(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="secret-a",
            category="A",
            version=1,
            lease_id="lease-a",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        results = db.list_all()
        for row in results:
            assert "value" not in row

    def test_list_all_empty(self, db: SecretMetaDB) -> None:
        assert db.list_all() == []


class TestRevoke:
    def test_revoke_returns_true_when_found(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-001",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        result = db.revoke("lease-001", "2026-01-02T00:00:00Z")
        assert result is True

    def test_revoke_returns_false_when_not_found(self, db: SecretMetaDB) -> None:
        result = db.revoke("nonexistent-lease", "2026-01-02T00:00:00Z")
        assert result is False

    def test_revoke_sets_revoked_at(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-001",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        db.revoke("lease-001", "2026-06-15T12:00:00Z")
        # Verify get_active no longer returns it
        assert db.get_active("my-secret") is None


class TestDeleteAllVersions:
    def test_delete_returns_count(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-v1",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        db.insert_version(
            name="my-secret",
            category="A",
            version=2,
            lease_id="lease-v2",
            issued_at="2026-01-02T00:00:00Z",
            expires_at=None,
            description="",
        )
        count = db.delete_all_versions("my-secret")
        assert count == 2

    def test_delete_removes_from_db(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="lease-001",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        db.delete_all_versions("my-secret")
        assert db.get_active("my-secret") is None
        assert db.list_all() == []

    def test_delete_returns_zero_for_nonexistent(self, db: SecretMetaDB) -> None:
        count = db.delete_all_versions("nonexistent")
        assert count == 0


class TestIdempotenceConstraints:
    def test_unique_lease_id_raises_on_duplicate(self, db: SecretMetaDB) -> None:
        db.insert_version(
            name="my-secret",
            category="A",
            version=1,
            lease_id="same-lease",
            issued_at="2026-01-01T00:00:00Z",
            expires_at=None,
            description="",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_version(
                name="other-secret",
                category="A",
                version=1,
                lease_id="same-lease",  # duplicate
                issued_at="2026-01-01T00:00:00Z",
                expires_at=None,
                description="",
            )


class TestSchemaCreation:
    def test_db_file_created_on_init(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "secrets.db"
        SecretMetaDB(db_path)
        assert db_path.exists()

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        """Calling twice on same path doesn't crash."""
        db_path = tmp_path / "secrets.db"
        SecretMetaDB(db_path)
        SecretMetaDB(db_path)  # should not raise
