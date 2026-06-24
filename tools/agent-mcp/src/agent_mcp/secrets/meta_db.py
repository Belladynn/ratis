"""SQLite metadata store for the secrets vault (Module 10).

Stores lease metadata for managed secrets. Values are NEVER stored here —
they live exclusively in the macOS Keychain. This DB is the inventory that
lets agents answer "what secrets exist" and "is this lease still active"
without touching the Keychain on every call.

Schema
------
secret_versions
    id          INTEGER PK AUTOINCREMENT
    name        TEXT NOT NULL          — logical secret name (e.g. "stripe-live-key")
    category    TEXT NOT NULL DEFAULT 'A'  — A=auto-generated, B=manually set, C=external
    version     INTEGER NOT NULL DEFAULT 1
    lease_id    TEXT NOT NULL UNIQUE   — opaque ID (UUID-like) for revocation targeting
    issued_at   TEXT NOT NULL          — ISO8601 UTC
    expires_at  TEXT                   — ISO8601 UTC, NULL = no TTL
    revoked_at  TEXT                   — ISO8601 UTC, NULL = still active
    description TEXT NOT NULL DEFAULT ''

Path resolution
---------------
See `agent_mcp.config.secrets_meta_db_file()` — honours
`RATIS_SECRETS_DB_PATH` env var for test isolation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS secret_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'A',
    version     INTEGER NOT NULL DEFAULT 1,
    lease_id    TEXT    NOT NULL UNIQUE,
    issued_at   TEXT    NOT NULL,
    expires_at  TEXT,
    revoked_at  TEXT,
    description TEXT    NOT NULL DEFAULT '',
    provider    TEXT,
    token_id    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_secret_versions_name ON secret_versions(name);
"""

_MIGRATION_ADD_PROVIDER_SQL = """
ALTER TABLE secret_versions ADD COLUMN provider TEXT;
"""

_MIGRATION_ADD_TOKEN_ID_SQL = "ALTER TABLE secret_versions ADD COLUMN token_id TEXT NOT NULL DEFAULT ''"  # noqa: S105

_ADMIN_EXPIRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS admin_token_expiry (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT    NOT NULL UNIQUE,
    keychain_service  TEXT    NOT NULL DEFAULT 'ratis-provider-admin',
    expires_at        TEXT,
    last_alerted_at   TEXT,
    notes             TEXT    NOT NULL DEFAULT ''
);
"""

_MIGRATION_ADD_ROTATION_WINDOW_SQL = "ALTER TABLE secret_versions ADD COLUMN rotation_window_expires_at TEXT"


class SecretMetaDB:
    """Lightweight SQLite store for secret version metadata.

    Thread-safety: SQLite ``check_same_thread=False`` plus Python's GIL are
    sufficient for the single-process MCP server use-case. If multi-process
    writes ever become needed, add WAL mode and retry on SQLITE_BUSY.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.executescript(_ADMIN_EXPIRY_SCHEMA_SQL)
        self._conn.commit()
        # Idempotent migrations: add columns if not present (existing DBs from PR 1/2).
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Add columns introduced in PR 3/7 to existing DBs (idempotent)."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(secret_versions)").fetchall()}
        if "provider" not in existing:
            self._conn.execute(_MIGRATION_ADD_PROVIDER_SQL)
            self._conn.commit()
        if "token_id" not in existing:
            self._conn.execute(_MIGRATION_ADD_TOKEN_ID_SQL)
            self._conn.commit()
        if "rotation_window_expires_at" not in existing:
            self._conn.execute(_MIGRATION_ADD_ROTATION_WINDOW_SQL)
            self._conn.commit()

    def insert_version(
        self,
        name: str,
        category: str,
        version: int,
        lease_id: str,
        issued_at: str,
        expires_at: str | None,
        description: str,
    ) -> int:
        """Insert a new secret version row. Returns the new row id.

        Raises ``sqlite3.IntegrityError`` if ``lease_id`` already exists
        (UNIQUE constraint) — callers should generate fresh UUIDs.
        """
        cur = self._conn.execute(
            """
            INSERT INTO secret_versions
                (name, category, version, lease_id, issued_at, expires_at, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, category, version, lease_id, issued_at, expires_at, description),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def insert_provision(
        self,
        name: str,
        version: int,
        lease_id: str,
        issued_at: str,
        expires_at: str | None,
        description: str,
        provider: str,
        token_id: str,
    ) -> int:
        """Insert a Cat-B provisioned token row. Returns the new row id."""
        cur = self._conn.execute(
            """
            INSERT INTO secret_versions
                (name, category, version, lease_id, issued_at, expires_at,
                 description, provider, token_id)
            VALUES (?, 'B', ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, version, lease_id, issued_at, expires_at, description, provider, token_id),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_by_lease_id(self, lease_id: str) -> dict | None:
        """Return the full row for a lease_id, or None if not found."""
        row = self._conn.execute(
            """
            SELECT id, name, category, version, lease_id,
                   issued_at, expires_at, revoked_at, description,
                   provider, token_id, rotation_window_expires_at
            FROM secret_versions
            WHERE lease_id = ?
            """,
            (lease_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_active(self, name: str) -> dict | None:
        """Return the most recent non-revoked version for ``name``, or None.

        "Active" means ``revoked_at IS NULL``. When multiple active versions
        exist (shouldn't happen in normal operation but possible), returns the
        highest ``version`` number.
        """
        row = self._conn.execute(
            """
            SELECT id, name, category, version, lease_id,
                   issued_at, expires_at, revoked_at, description
            FROM secret_versions
            WHERE name = ? AND revoked_at IS NULL
            ORDER BY version DESC
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_all(self) -> list[dict]:
        """Return metadata for all known secrets (one row per name, latest version).

        Values are NEVER included — this is a metadata-only view.
        """
        rows = self._conn.execute(
            """
            SELECT name, category, version, lease_id, issued_at, expires_at, revoked_at
            FROM secret_versions sv1
            WHERE id = (
                SELECT id FROM secret_versions sv2
                WHERE sv2.name = sv1.name
                ORDER BY version DESC
                LIMIT 1
            )
            ORDER BY name
            """,
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, lease_id: str, revoked_at: str) -> bool:
        """Mark a lease as revoked. Returns True if the lease was found.

        Idempotent: calling twice with the same ``lease_id`` updates
        ``revoked_at`` again but does not raise.
        """
        cur = self._conn.execute(
            "UPDATE secret_versions SET revoked_at = ? WHERE lease_id = ?",
            (revoked_at, lease_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_all_versions(self, name: str) -> int:
        """Delete all version rows for ``name``. Returns the number of rows removed.

        Used by ``secret_delete`` — Keychain deletion happens separately.
        """
        cur = self._conn.execute(
            "DELETE FROM secret_versions WHERE name = ?",
            (name,),
        )
        self._conn.commit()
        return cur.rowcount

    # ---------------------------------------------------------------------------
    # admin_token_expiry table (PR 6)
    # ---------------------------------------------------------------------------

    def upsert_admin_expiry(
        self,
        provider: str,
        expires_at: str | None,
        notes: str = "",
    ) -> None:
        """INSERT OR REPLACE a provider entry in admin_token_expiry.

        Resets ``last_alerted_at`` to NULL on every upsert so a fresh alert
        can be sent after updating an expiry date.
        """
        self._conn.execute(
            """
            INSERT INTO admin_token_expiry (provider, expires_at, last_alerted_at, notes)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(provider) DO UPDATE SET
                expires_at      = excluded.expires_at,
                last_alerted_at = NULL,
                notes           = excluded.notes
            """,
            (provider, expires_at, notes),
        )
        self._conn.commit()

    def list_admin_expiry(self) -> list[dict]:
        """Return all rows in admin_token_expiry."""
        rows = self._conn.execute(
            "SELECT provider, keychain_service, expires_at, last_alerted_at, notes"
            " FROM admin_token_expiry ORDER BY provider"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_expiring_soon(self, days: int = 60) -> list[dict]:
        """Return entries whose ``expires_at`` is before ``now + days``.

        Entries with ``expires_at IS NULL`` are excluded (unknown = ignored).
        """
        import datetime as _dt

        cutoff = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT provider, keychain_service, expires_at, last_alerted_at, notes
            FROM admin_token_expiry
            WHERE expires_at IS NOT NULL AND expires_at < ?
            ORDER BY expires_at
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_alerted(self, provider: str, alerted_at: str) -> None:
        """Update ``last_alerted_at`` for the given provider."""
        self._conn.execute(
            "UPDATE admin_token_expiry SET last_alerted_at = ? WHERE provider = ?",
            (alerted_at, provider),
        )
        self._conn.commit()

    # -----------------------------------------------------------------------
    # rotation + rollback support (PR 7)
    # -----------------------------------------------------------------------

    def get_by_name_version(self, name: str, version: int) -> dict | None:
        """Return the row for (name, version), regardless of revoked status."""
        row = self._conn.execute(
            """
            SELECT id, name, category, version, lease_id,
                   issued_at, expires_at, revoked_at, description,
                   provider, token_id, rotation_window_expires_at
            FROM secret_versions
            WHERE name = ? AND version = ?
            LIMIT 1
            """,
            (name, version),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def set_rotation_window(self, lease_id: str, window_expires_at: str) -> bool:
        """Set the rotation_window_expires_at on a row identified by lease_id.

        Returns True if the row was found and updated.
        """
        cur = self._conn.execute(
            "UPDATE secret_versions SET rotation_window_expires_at = ? WHERE lease_id = ?",
            (window_expires_at, lease_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def reactivate_version(self, name: str, version: int) -> bool:
        """Clear revoked_at for (name, version), making it active again.

        Returns True if the row was found and updated.
        """
        cur = self._conn.execute(
            "UPDATE secret_versions SET revoked_at = NULL WHERE name = ? AND version = ?",
            (name, version),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_expired_rotation_windows(self, now_iso: str) -> list[dict]:
        """Return rows where rotation_window_expires_at < now_iso and revoked_at IS NULL."""
        rows = self._conn.execute(
            """
            SELECT id, name, category, version, lease_id,
                   issued_at, expires_at, revoked_at, description,
                   rotation_window_expires_at
            FROM secret_versions
            WHERE rotation_window_expires_at IS NOT NULL
              AND rotation_window_expires_at < ?
              AND revoked_at IS NULL
            """,
            (now_iso,),
        ).fetchall()
        return [dict(row) for row in rows]
