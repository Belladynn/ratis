"""Tests for migration ``20260501_1700_nrcC`` — NRC bloc C partial index.

Validates that the partial index ``idx_pal_consensus_state_changed`` on
``pipeline_audit_log`` is created on upgrade and dropped on downgrade.
The index speeds up ``was_ever_verified()`` lookups for the consensus
state machine — see
``webservices/ratis_product_analyser/repositories/name_resolution_repository.py``.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

# Revision under test
TARGET_REVISION = "20260501_1700_nrcC"
PREV_REVISION = "20260501_1500_supid"

ALEMBIC_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Run alembic upgrade to ``TARGET_REVISION``.

    Pre-step : wipe ``public`` schema so a previous module's leftover
    tables cannot fail this run's upgrade chain on an unrelated
    object. We do NOT teardown the schema ourselves — the session
    fixture in :mod:`conftest` handles the final cleanup. This avoids
    cross-test cascade-drop quirks observed when chaining
    ``downgrade base`` across heterogeneous migration modules.
    """
    from sqlalchemy import text as _text

    with migration_engine.connect() as conn:
        conn.execute(_text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


def test_partial_index_exists_post_upgrade(upgraded_engine):
    """The partial index must exist with the expected ``WHERE`` predicate
    so PG can use it for the ``was_ever_verified()`` lookup."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'idx_pal_consensus_state_changed'
                """
            )
        ).first()
    assert row is not None, "partial index idx_pal_consensus_state_changed missing"
    indexdef = row.indexdef.lower()
    # Core invariants : event column + partial WHERE on event value.
    assert "event" in indexdef
    assert "consensus_state_changed" in indexdef


def test_partial_index_dropped_on_downgrade(upgraded_engine):
    """``downgrade -1`` removes the index without touching the underlying table.

    Runs AFTER the existence test (alphabetical order : exists < dropped).
    Re-upgrades back to ``TARGET_REVISION`` at the end so the module
    fixture's teardown still reaches the bottom cleanly.
    """
    cfg = _make_alembic_config(upgraded_engine)
    command.downgrade(cfg, PREV_REVISION)
    try:
        with upgraded_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname = 'idx_pal_consensus_state_changed'
                    """
                )
            ).first()
        assert row is None
    finally:
        # Restore the upgraded state for the module fixture's teardown.
        command.upgrade(cfg, TARGET_REVISION)
