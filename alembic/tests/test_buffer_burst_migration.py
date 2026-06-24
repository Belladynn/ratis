"""Migration tests for ``20260509_1200_bbv1`` — Buffer + Burst V1.

Validates the upgrade chain :

* ``user_missions.boost_count`` renamed to ``buffer_count``
* 4 new columns added with proper defaults
* Table ``stonks_records`` dropped
* Table ``mission_xp_records`` created with FKs + indexes + UNIQUE
* ``xp_transactions`` CHECK constraint accepts ``mission_burst``

Mirrors the pattern of ``test_admin_settings_audit_migration.py`` —
isolated migration_engine, full alembic upgrade chain.
"""
from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text


TARGET_REVISION = "20260509_1200_bbv1"
PREV_REVISION = "20260509_0100_disqual"

ALEMBIC_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "alembic.ini"
)


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Wipe + run alembic upgrade up to the migration under test."""
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


def test_user_missions_buffer_count_exists(upgraded_engine):
    """boost_count renamed → buffer_count + boost_count gone."""
    with upgraded_engine.connect() as conn:
        cols = {
            r.column_name
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user_missions'"
                )
            )
        }
    assert "buffer_count" in cols
    assert "boost_count" not in cols


def test_user_missions_new_columns(upgraded_engine):
    """4 nouvelles colonnes : burst_count, period_extended_until,
    burst_locked, portions_claimed."""
    with upgraded_engine.connect() as conn:
        cols = {
            r.column_name: (r.is_nullable, r.column_default)
            for r in conn.execute(
                text(
                    "SELECT column_name, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'user_missions'"
                )
            )
        }
    assert cols["burst_count"] == ("NO", "0")
    assert cols["portions_claimed"] == ("NO", "0")
    assert cols["burst_locked"] == ("NO", "false")
    # period_extended_until is nullable, no default
    assert cols["period_extended_until"][0] == "YES"


def test_stonks_records_dropped(upgraded_engine):
    """Table stonks_records ne doit plus exister."""
    with upgraded_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'stonks_records'"
            )
        ).first()
    assert result is None


def test_mission_xp_records_created(upgraded_engine):
    """Table mission_xp_records doit exister avec colonnes attendues."""
    with upgraded_engine.connect() as conn:
        cols = {
            r.column_name: r.data_type
            for r in conn.execute(
                text(
                    "SELECT column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'mission_xp_records'"
                )
            )
        }
    expected = {
        "id",
        "user_id",
        "mission_id",
        "user_mission_id",
        "xp_earned",
        "burst_count",
        "buffer_count",
        "recorded_at",
    }
    assert expected.issubset(cols.keys())


def test_mission_xp_records_unique_user_mission(upgraded_engine):
    """UNIQUE (user_mission_id) — 1 record par mission complétée."""
    with upgraded_engine.connect() as conn:
        constraints = {
            r.constraint_name
            for r in conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'mission_xp_records' "
                    "AND constraint_type = 'UNIQUE'"
                )
            )
        }
    assert "uq_mxr_user_mission" in constraints


def test_xp_reason_check_accepts_mission_burst(upgraded_engine):
    """xp_transactions CHECK accepte 'mission_burst' après migration.

    Crée un user de test puis insère une row xp_transactions avec
    reason='mission_burst' — pas de violation de CHECK constraint.
    """
    with upgraded_engine.connect() as conn:
        # Need a user row first (FK)
        uid = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO users (id, email, support_id, provider, password_hash, "
                "                  created_at, updated_at) "
                "VALUES (:id, :email, :sid, 'email', 'hashed', now(), now())"
            ),
            {"id": uid, "email": f"u{uid.hex[:8]}@t.com", "sid": uid.hex[:10]},
        )
        # Insert a Burst XP credit — should not raise
        conn.execute(
            text(
                "INSERT INTO xp_transactions (id, user_id, amount, reason) "
                "VALUES (:id, :uid, 10, 'mission_burst')"
            ),
            {"id": uuid.uuid4(), "uid": uid},
        )
        conn.commit()


def test_downgrade_then_upgrade_roundtrip(upgraded_engine):
    """downgrade → upgrade : assure que le couple est idempotent.

    Run après les autres tests du module pour ne pas casser leur état.
    """
    cfg = _make_alembic_config(upgraded_engine)
    command.downgrade(cfg, PREV_REVISION)
    # boost_count est revenu, buffer_count parti
    with upgraded_engine.connect() as conn:
        cols = {
            r.column_name
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user_missions'"
                )
            )
        }
        assert "boost_count" in cols
        assert "buffer_count" not in cols
        # stonks_records re-created
        srec = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'stonks_records'"
            )
        ).first()
        assert srec is not None

    # Re-upgrade
    command.upgrade(cfg, TARGET_REVISION)
    with upgraded_engine.connect() as conn:
        cols = {
            r.column_name
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user_missions'"
                )
            )
        }
        assert "buffer_count" in cols
        assert "boost_count" not in cols
