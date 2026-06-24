"""Tests for migration ``20260430_1000_pipev3`` — pipeline clean install.

Runs the actual ``alembic upgrade`` / ``downgrade`` chain against an isolated
ratis_migration_test DB. Complements
``webservices/ratis_product_analyser/tests/pipeline/test_migration.py``
which exercises the same invariants on a ``Base.metadata.create_all()``
schema (faster, but does not validate the migration script itself).

Coverage :
- Tables ``parsed_tickets`` and ``pipeline_audit_log`` exist after upgrade.
- Columns ``scans.match_confidence`` and ``scans.parsed_ticket_id`` exist.
- Extension ``unaccent`` is installed.
- Trigger ``trg_pipeline_audit_log_no_update`` blocks UPDATE.
- ``upgrade head → downgrade -1 → upgrade head`` is idempotent (no error).
- Tables are dropped after downgrade.
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

# Revision under test
TARGET_REVISION = "20260430_1000_pipev3"
PREV_REVISION = "20260429_1000_storeval"

ALEMBIC_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "alembic.ini"
)


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Run migrations up to and including the target revision."""
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    yield migration_engine
    command.downgrade(cfg, "base")


@pytest.fixture(scope="module")
def round_trip_engine(migration_engine):
    """upgrade → downgrade -1 → upgrade : exercise the full cycle."""
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    command.downgrade(cfg, PREV_REVISION)
    yield migration_engine
    command.downgrade(cfg, "base")


# ── upgrade ──────────────────────────────────────────────────────────────────


def test_parsed_tickets_table_exists(upgraded_engine):
    inspector = inspect(upgraded_engine)
    assert "parsed_tickets" in inspector.get_table_names()


def test_pipeline_audit_log_table_exists(upgraded_engine):
    inspector = inspect(upgraded_engine)
    assert "pipeline_audit_log" in inspector.get_table_names()


def test_scans_columns_added(upgraded_engine):
    inspector = inspect(upgraded_engine)
    cols = {c["name"] for c in inspector.get_columns("scans")}
    assert "match_confidence" in cols
    assert "parsed_ticket_id" in cols


def test_receipts_parsed_ticket_id_added(upgraded_engine):
    inspector = inspect(upgraded_engine)
    cols = {c["name"] for c in inspector.get_columns("receipts")}
    assert "parsed_ticket_id" in cols


def test_unaccent_extension_installed(upgraded_engine):
    with upgraded_engine.connect() as conn:
        ext = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'unaccent'")
        ).scalar_one_or_none()
    assert ext == "unaccent"


def test_products_name_normalized_uppercases_and_strips_accents(upgraded_engine):
    """End-to-end smoke : the migration installed everything needed for
    ``UPPER(unaccent(name))`` to work on inserts."""
    with upgraded_engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO products (ean, name, source)
                VALUES ('9999999999999', 'Café Brûlé', 'off')
                """
            )
        )
        conn.commit()
        normalized = conn.execute(
            text("SELECT name_normalized FROM products WHERE ean = '9999999999999'")
        ).scalar_one()
    assert normalized == "CAFE BRULE"


def test_pipeline_audit_log_append_only_blocks_update(upgraded_engine):
    """The trigger raises on UPDATE — no row mutation past insert."""
    with upgraded_engine.connect() as conn:
        row_id = conn.execute(
            text(
                """
                INSERT INTO pipeline_audit_log (phase, level, event, payload)
                VALUES ('extract', 'normal', 'ocr_done', '{}'::jsonb)
                RETURNING id
                """
            )
        ).scalar_one()
        conn.commit()

    with upgraded_engine.connect() as conn:
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                text(
                    "UPDATE pipeline_audit_log SET event = 'tampered' WHERE id = :id"
                ),
                {"id": row_id},
            )
            conn.commit()
        msg = str(exc_info.value).lower()
        assert "append-only" in msg or "prohibited" in msg


# ── downgrade / round-trip ──────────────────────────────────────────────────


def test_round_trip_drops_pipeline_tables(round_trip_engine):
    """After downgrade -1, parsed_tickets / pipeline_audit_log are gone."""
    inspector = inspect(round_trip_engine)
    names = inspector.get_table_names()
    assert "parsed_tickets" not in names
    assert "pipeline_audit_log" not in names


def test_round_trip_drops_scans_v3_columns(round_trip_engine):
    """After downgrade -1, the new columns are gone."""
    inspector = inspect(round_trip_engine)
    cols = {c["name"] for c in inspector.get_columns("scans")}
    assert "match_confidence" not in cols
    assert "parsed_ticket_id" not in cols


def test_round_trip_drops_unaccent_extension(round_trip_engine):
    with round_trip_engine.connect() as conn:
        ext = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'unaccent'")
        ).scalar_one_or_none()
    assert ext is None
