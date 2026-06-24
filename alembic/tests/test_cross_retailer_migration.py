"""Tests for migration ``20260502_1900_xretail`` — cross-retailer consensus.

Validates that the bloc A schema additions land cleanly :

- columns ``source_type`` (NOT NULL DEFAULT 'receipt') and ``retailer_id``
  (NULLABLE FK retailers ON DELETE RESTRICT) added on ``product_name_resolutions``
- CHECK ``pnr_match_method_check`` accepts ``'esl'`` and
  ``'cross_source_esl_exact'``
- UNIQUE INDEX migration : ``idx_pnr_scan_source_label`` exists on
  (scan_id, source_type, normalized_label) ; the legacy
  ``idx_pnr_scan_label`` is gone.
- New partial indexes ``idx_pnr_retailer_source_label`` and
  ``idx_pnr_norm_label_trgm`` exist with ``WHERE retailer_id IS NOT NULL``.
- Trigger ``trg_pnr_sync_retailer_id`` exists on the table.
- Downgrade restores the previous shape : drops the new columns / indexes
  / constraint additions / trigger / function.

See ``ARCH_cross_retailer_consensus.md`` § Schéma DB / § Migration data.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

# Revision under test
TARGET_REVISION = "20260502_1900_xretail"
PREV_REVISION = "20260502_1700_consmatch"

ALEMBIC_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Wipe schema then run alembic upgrade to ``TARGET_REVISION``.

    Pre-step : DROP SCHEMA / CREATE SCHEMA so a previous module's leftover
    tables cannot fail this run's upgrade chain on an unrelated object.
    See sibling ``test_nrc_c_audit_idx_migration.py`` for the same pattern.
    """
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


# ── upgrade : columns ─────────────────────────────────────────────────────────

def test_source_type_column_exists(upgraded_engine):
    inspector = inspect(upgraded_engine)
    cols = {c["name"]: c for c in inspector.get_columns("product_name_resolutions")}
    assert "source_type" in cols
    assert cols["source_type"]["nullable"] is False


def test_source_type_default_is_receipt(upgraded_engine):
    """Existing INSERT call-sites (bloc A scope) omit source_type → DEFAULT."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT column_default
                FROM information_schema.columns
                WHERE table_name = 'product_name_resolutions'
                  AND column_name = 'source_type'
                """
            )
        ).first()
    assert row is not None
    assert "receipt" in (row.column_default or "")


def test_retailer_id_column_exists_nullable(upgraded_engine):
    inspector = inspect(upgraded_engine)
    cols = {c["name"]: c for c in inspector.get_columns("product_name_resolutions")}
    assert "retailer_id" in cols
    assert cols["retailer_id"]["nullable"] is True


def test_retailer_id_fk_on_delete_restrict(upgraded_engine):
    """FK retailer_id → retailers(id) ON DELETE RESTRICT.

    RESTRICT (not CASCADE) is intentional — a retailer must never vanish
    silently while a ledger row references it (R05 + audit).
    """
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT rc.delete_rule
                FROM information_schema.referential_constraints rc
                JOIN information_schema.key_column_usage kcu
                  ON rc.constraint_name = kcu.constraint_name
                WHERE kcu.table_name = 'product_name_resolutions'
                  AND kcu.column_name = 'retailer_id'
                """
            )
        ).first()
    assert row is not None
    assert row.delete_rule == "RESTRICT"


# ── upgrade : CHECK match_method ──────────────────────────────────────────────

def _check_constraint_def(engine, name: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid) AS constraint_def
                FROM pg_constraint
                WHERE conname = :n
                """
            ),
            {"n": name},
        ).first()
    assert row is not None, f"constraint {name} missing"
    return row.constraint_def


def test_check_match_method_accepts_esl(upgraded_engine):
    """CHECK must accept the new ``'esl'`` literal."""
    cdef = _check_constraint_def(upgraded_engine, "pnr_match_method_check")
    assert "'esl'" in cdef


def test_check_match_method_accepts_cross_source_esl_exact(upgraded_engine):
    cdef = _check_constraint_def(upgraded_engine, "pnr_match_method_check")
    assert "'cross_source_esl_exact'" in cdef


def test_check_source_type_constraint_exists(upgraded_engine):
    cdef = _check_constraint_def(upgraded_engine, "pnr_source_type_check")
    assert "'receipt'" in cdef
    assert "'esl'" in cdef


# ── upgrade : indexes ─────────────────────────────────────────────────────────

def test_legacy_unique_index_dropped(upgraded_engine):
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'idx_pnr_scan_label'
                """
            )
        ).first()
    assert row is None


def test_new_unique_index_exists(upgraded_engine):
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'idx_pnr_scan_source_label'
                """
            )
        ).first()
    assert row is not None
    indexdef = row.indexdef.lower()
    # Composite UNIQUE on (scan_id, source_type, normalized_label).
    assert "unique" in indexdef
    assert "scan_id" in indexdef
    assert "source_type" in indexdef
    assert "normalized_label" in indexdef


def test_retailer_source_label_partial_index(upgraded_engine):
    """Hot path index : btree on (retailer_id, source_type, normalized_label)
    partial WHERE retailer_id IS NOT NULL."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'idx_pnr_retailer_source_label'
                """
            )
        ).first()
    assert row is not None
    indexdef = row.indexdef.lower()
    assert "retailer_id" in indexdef
    assert "source_type" in indexdef
    assert "normalized_label" in indexdef
    assert "where" in indexdef
    assert "retailer_id is not null" in indexdef


def test_norm_label_gin_trgm_partial_index(upgraded_engine):
    """Fuzzy retailer-wide path : GIN gin_trgm_ops on normalized_label,
    partial WHERE retailer_id IS NOT NULL."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'idx_pnr_norm_label_trgm'
                """
            )
        ).first()
    assert row is not None
    indexdef = row.indexdef.lower()
    assert "gin" in indexdef
    assert "gin_trgm_ops" in indexdef
    assert "where" in indexdef
    assert "retailer_id is not null" in indexdef


# ── upgrade : trigger ─────────────────────────────────────────────────────────

def test_trigger_exists(upgraded_engine):
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgname = 'trg_pnr_sync_retailer_id'
                  AND tgrelid = 'product_name_resolutions'::regclass
                  AND NOT tgisinternal
                """
            )
        ).first()
    assert row is not None


def test_trigger_function_exists(upgraded_engine):
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT proname FROM pg_proc
                WHERE proname = 'fn_sync_pnr_retailer_id'
                """
            )
        ).first()
    assert row is not None


# ── downgrade ─────────────────────────────────────────────────────────────────

def test_downgrade_reverts_schema(upgraded_engine):
    """``downgrade -1`` removes the new columns / indexes / trigger and
    restores the legacy UNIQUE index. Re-upgrades at the end so the
    module fixture's teardown still reaches the bottom cleanly.
    """
    cfg = _make_alembic_config(upgraded_engine)
    command.downgrade(cfg, PREV_REVISION)
    try:
        inspector = inspect(upgraded_engine)
        cols = {c["name"] for c in inspector.get_columns("product_name_resolutions")}
        assert "source_type" not in cols
        assert "retailer_id" not in cols
        with upgraded_engine.connect() as conn:
            # Trigger gone.
            trg = conn.execute(
                text(
                    "SELECT 1 FROM pg_trigger "
                    "WHERE tgname = 'trg_pnr_sync_retailer_id'"
                )
            ).first()
            assert trg is None
            # Function gone.
            fn = conn.execute(
                text(
                    "SELECT 1 FROM pg_proc "
                    "WHERE proname = 'fn_sync_pnr_retailer_id'"
                )
            ).first()
            assert fn is None
            # Legacy UNIQUE index restored.
            legacy = conn.execute(
                text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE indexname = 'idx_pnr_scan_label'"
                )
            ).first()
            assert legacy is not None
            # New indexes gone.
            for idx in (
                "idx_pnr_scan_source_label",
                "idx_pnr_retailer_source_label",
                "idx_pnr_norm_label_trgm",
            ):
                gone = conn.execute(
                    text(
                        "SELECT 1 FROM pg_indexes WHERE indexname = :i"
                    ),
                    {"i": idx},
                ).first()
                assert gone is None, f"{idx} not dropped on downgrade"
    finally:
        command.upgrade(cfg, TARGET_REVISION)
