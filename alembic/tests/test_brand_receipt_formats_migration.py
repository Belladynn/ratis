"""Tests for migration 20260418_1000_d6e5f4a3b2c1 — brand_receipt_formats.

TDD: these tests are written before the migration exists and must fail first.

Tests cover:
  - Table exists after upgrade
  - Seed rows for intermarche and monoprix are present
  - JSONB fields contain the expected store_code entry
  - Table disappears after downgrade
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

# Revision under test
TARGET_REVISION = "d6e5f4a3b2c1"
PREV_REVISION = "c5d4e3f2a1b0"

ALEMBIC_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Run all migrations up to (and including) the target revision."""
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    yield migration_engine
    # Downgrade is tested in a separate test; here we clean up fully
    command.downgrade(cfg, "base")


@pytest.fixture(scope="module")
def downgrade_engine(migration_engine):
    """Separate fixture: upgrades to target then downgrades one step."""
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    command.downgrade(cfg, PREV_REVISION)
    yield migration_engine
    command.downgrade(cfg, "base")


# ── upgrade tests ─────────────────────────────────────────────────────────────

def test_table_exists_after_upgrade(upgraded_engine):
    """brand_receipt_formats table must exist after upgrade."""
    inspector = inspect(upgraded_engine)
    assert "brand_receipt_formats" in inspector.get_table_names()


def test_seed_intermarche_present(upgraded_engine):
    """intermarche row must be seeded."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text("SELECT brand_key, length FROM brand_receipt_formats WHERE brand_key = 'intermarche'")
        ).fetchone()
    assert row is not None
    assert row.brand_key == "intermarche"
    assert row.length == 24


def test_seed_monoprix_present(upgraded_engine):
    """monoprix row must be seeded."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text("SELECT brand_key, length FROM brand_receipt_formats WHERE brand_key = 'monoprix'")
        ).fetchone()
    assert row is not None
    assert row.brand_key == "monoprix"
    assert row.length == 24


def test_intermarche_fields_contain_store_code(upgraded_engine):
    """intermarche fields JSONB must contain a store_code entry at positions 19-24."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT fields FROM brand_receipt_formats WHERE brand_key = 'intermarche'"
            )
        ).fetchone()
    assert row is not None
    fields = row.fields
    store_code_field = next((f for f in fields if f["name"] == "store_code"), None)
    assert store_code_field is not None, "store_code field not found in intermarche fields"
    assert store_code_field["start"] == 19
    assert store_code_field["end"] == 24


def test_monoprix_fields_contain_store_code(upgraded_engine):
    """monoprix fields JSONB must contain a store_code entry at positions 0-4."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT fields FROM brand_receipt_formats WHERE brand_key = 'monoprix'"
            )
        ).fetchone()
    assert row is not None
    fields = row.fields
    store_code_field = next((f for f in fields if f["name"] == "store_code"), None)
    assert store_code_field is not None, "store_code field not found in monoprix fields"
    assert store_code_field["start"] == 0
    assert store_code_field["end"] == 4


def test_both_seeds_only(upgraded_engine):
    """Exactly two rows must be seeded (intermarche and monoprix)."""
    with upgraded_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM brand_receipt_formats")
        ).scalar()
    assert count == 2


# ── downgrade tests ────────────────────────────────────────────────────────────

def test_table_gone_after_downgrade(downgrade_engine):
    """brand_receipt_formats must not exist after downgrade."""
    inspector = inspect(downgrade_engine)
    assert "brand_receipt_formats" not in inspector.get_table_names()
