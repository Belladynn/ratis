"""Tests for migration ``20260502_1900_admauad`` — admin_settings_audit.

Validates :

- ``upgrade()`` creates the ``admin_settings_audit`` table, the
  ``admin_settings_audit_status`` ENUM type and the three indexes
  (``idx_admin_settings_audit_section_ts``, ``idx_admin_settings_audit_ts``,
  partial ``idx_admin_settings_audit_pending``).
- The two CHECK constraints (``chk_reason_min_len``,
  ``chk_status_2fa_coherence``) are active — a direct INSERT bypassing
  the model raises ``IntegrityError`` on each violation.
- ``downgrade()`` drops both the table and the ENUM cleanly, and
  ``upgrade()`` is idempotently re-runnable afterwards.
"""

from __future__ import annotations

import json
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


TARGET_REVISION = "20260502_1900_admauad"
PREV_REVISION = "20260502_1700_consmatch"

ALEMBIC_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Wipe + run alembic upgrade up to the migration under test.

    Pattern mirrors ``test_nrc_c_audit_idx_migration.py`` : we DROP and
    re-CREATE the public schema before running the upgrade chain so a
    sibling module's leftover state cannot break this run.
    """
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


def test_table_exists_with_expected_columns(upgraded_engine):
    """Sanity check : table is in place with the documented column set."""
    with upgraded_engine.connect() as conn:
        cols = {
            r.column_name: r.data_type
            for r in conn.execute(
                text(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = 'admin_settings_audit'
                    """
                )
            )
        }
    expected = {
        "id",
        "timestamp",
        "operator",
        "section",
        "reason",
        "old_data",
        "new_data",
        "diff",
        "status",
        "expires_at",
        "applied_at",
    }
    assert expected.issubset(cols.keys()), f"missing cols: {expected - cols.keys()}"
    assert cols["old_data"] == "jsonb"
    assert cols["new_data"] == "jsonb"
    assert cols["status"] == "USER-DEFINED"  # the ENUM type


def test_enum_type_exists_with_expected_labels(upgraded_engine):
    """The native PG ENUM must exist and have the four documented labels
    in the order declared by the migration."""
    with upgraded_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'admin_settings_audit_status'
                ORDER BY e.enumsortorder
                """
            )
        ).fetchall()
    labels = [r.enumlabel for r in rows]
    assert labels == ["applied", "pending_2fa", "expired", "cancelled"]


def test_three_indexes_exist(upgraded_engine):
    """All three indexes from the ARCH must be present, including the
    partial ``WHERE status = 'pending_2fa'``."""
    with upgraded_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'admin_settings_audit'
                """
            )
        ).fetchall()
    by_name = {r.indexname: r.indexdef.lower() for r in rows}
    assert "idx_admin_settings_audit_section_ts" in by_name
    assert "idx_admin_settings_audit_ts" in by_name
    assert "idx_admin_settings_audit_pending" in by_name
    # Partial predicate proves the index is the cleanup-batch friendly one.
    pending_def = by_name["idx_admin_settings_audit_pending"]
    assert "where" in pending_def
    assert "status" in pending_def
    assert "pending_2fa" in pending_def


def test_check_reason_min_length_active(upgraded_engine):
    """Direct INSERT with a 5-char reason must be rejected by the CHECK."""
    with upgraded_engine.connect() as conn:
        with conn.begin():
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO admin_settings_audit"
                        " (operator, section, reason, new_data, status, applied_at)"
                        " VALUES (:op, :sec, 'short', CAST(:data AS JSONB), 'applied', now())"
                    ),
                    {
                        "op": "probe",
                        "sec": "rewards",
                        "data": json.dumps({"x": 1}),
                    },
                )


def test_check_status_coherence_active(upgraded_engine):
    """``status='applied'`` with ``applied_at=NULL`` must be rejected."""
    with upgraded_engine.connect() as conn:
        with conn.begin():
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO admin_settings_audit"
                        " (operator, section, reason, new_data, status, applied_at)"
                        " VALUES (:op, :sec, :reason, CAST(:data AS JSONB), 'applied', NULL)"
                    ),
                    {
                        "op": "probe",
                        "sec": "rewards",
                        "reason": "manual probe insert",
                        "data": json.dumps({"x": 1}),
                    },
                )


def test_check_status_coherence_pending_requires_expires_at(upgraded_engine):
    """``status='pending_2fa'`` with ``expires_at=NULL`` must be rejected."""
    with upgraded_engine.connect() as conn:
        with conn.begin():
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO admin_settings_audit"
                        " (operator, section, reason, new_data, status,"
                        " expires_at, applied_at)"
                        " VALUES (:op, :sec, :reason, CAST(:data AS JSONB),"
                        " 'pending_2fa', NULL, NULL)"
                    ),
                    {
                        "op": "probe",
                        "sec": "rewards",
                        "reason": "manual probe pending",
                        "data": json.dumps({"x": 1}),
                    },
                )


def test_downgrade_drops_table_and_enum(upgraded_engine):
    """``downgrade -1`` must drop both the table AND the ENUM type ;
    re-upgrading then yields a clean state.

    Runs LAST in alphabetical order so earlier tests can still use the
    upgraded schema (pytest sorts tests by source order, but module
    fixtures are scoped — we re-upgrade in ``finally`` to keep teardown
    deterministic).
    """
    cfg = _make_alembic_config(upgraded_engine)
    command.downgrade(cfg, PREV_REVISION)
    try:
        with upgraded_engine.connect() as conn:
            tbl = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables"
                    " WHERE table_name = 'admin_settings_audit'"
                )
            ).first()
            assert tbl is None
            enum_row = conn.execute(
                text(
                    "SELECT 1 FROM pg_type"
                    " WHERE typname = 'admin_settings_audit_status'"
                )
            ).first()
            assert enum_row is None
    finally:
        command.upgrade(cfg, TARGET_REVISION)


def test_upgrade_idempotent_after_downgrade(upgraded_engine):
    """After the previous test's down → up cycle, the table must still
    work — re-insert a valid row to prove the schema is functional."""
    with upgraded_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO admin_settings_audit"
                    " (operator, section, reason, new_data, status, applied_at)"
                    " VALUES (:op, :sec, :reason, CAST(:data AS JSONB),"
                    " 'applied', now())"
                ),
                {
                    "op": "probe",
                    "sec": "rewards",
                    "reason": "post-cycle smoke test write",
                    "data": json.dumps({"x": 1}),
                },
            )
            row = conn.execute(
                text(
                    "SELECT operator FROM admin_settings_audit"
                    " WHERE section = 'rewards' AND operator = 'probe'"
                    " ORDER BY timestamp DESC LIMIT 1"
                )
            ).first()
            assert row is not None
            assert row.operator == "probe"
