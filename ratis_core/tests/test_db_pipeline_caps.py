"""Tests for the dormant pipeline caps trigger ``fn_db_pipeline_caps_enforce()``.

HSP2 §3 — double kill-switch :
- transaction-local ``SET LOCAL app.caps_enforced = 'true'`` AND
- ``app_settings.db_pipeline_caps -> 'caps_enforced'`` == ``true``.

Without both, the trigger is a no-op (mode bootstrap intact). Both armed,
the trigger enforces :
- per-user 24h credit cumul > 5000 → EXCEPTION
- global 24h credit cumul     > 50000 → EXCEPTION
- global 24h credit cumul     > 20000 → WARNING (no rollback)
- direction = 'debit' is not counted

Tests use the alembic-spinup fixture so the trigger + the seed of
``app_settings.db_pipeline_caps`` (caps_enforced=false initially) are
in place.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InternalError, ProgrammingError

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def hsp2_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp2_caps")


@pytest.fixture
def engine(hsp2_db_url: str) -> Iterator[Engine]:
    eng = create_engine(hsp2_db_url)
    try:
        # Reset between tests : truncate the moving tables ; reset the
        # caps setting to its seeded state (caps_enforced=false).
        with eng.begin() as conn:
            conn.execute(text("TRUNCATE TABLE db_change_log"))
            conn.execute(text("TRUNCATE TABLE cabecoin_transactions CASCADE"))
            conn.execute(
                text(
                    "UPDATE app_settings "
                    "SET data = jsonb_set(data, '{caps_enforced}', 'false'::jsonb) "
                    "WHERE section = 'db_pipeline_caps'"
                )
            )
        yield eng
    finally:
        eng.dispose()


def _seed_user(conn) -> uuid.UUID:
    """Insert a minimal user row.

    Migration 20260518_1300_users_account_type renamed ``users.provider`` →
    ``users.account_type`` (values: oauth|internal|deleted|dev) and dropped
    ``users.provider_id``. We use account_type='internal' which satisfies
    the ``account_type_check`` CHECK without needing password_hash.

    ``support_id`` (unique, NOT NULL, no server default) and
    ``gift_card_redeemed_ytd_cents`` (NOT NULL, no server default) must be
    supplied explicitly — R33: schema is the source of truth.
    """
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users "
            "(id, email, account_type, support_id, gift_card_redeemed_ytd_cents) "
            "VALUES (:id, :email, 'internal', :sup, 0)"
        ),
        {"id": user_id, "email": f"u-{user_id}@example.test", "sup": f"sup-{user_id.hex[:8]}"},
    )
    return user_id


def test_caps_dormant_by_default_large_insert_passes(engine):
    """No ``app.caps_enforced`` set + caps_enforced=false in settings →
    a 1,000,000 CAB credit goes through. Bootstrap intact."""
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 1000000, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM cabecoin_transactions")).scalar()
        assert n == 1


def test_caps_active_global_block_at_50k(engine):
    """Both switches armed : 50_000 cumul + 1 more = block."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app_settings "
                "SET data = jsonb_set(data, '{caps_enforced}', 'true'::jsonb) "
                "WHERE section = 'db_pipeline_caps'"
            )
        )
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        # Insert without enforcement first to fill the 24h window — the
        # caps fire only on the *new* insert when v_session_enforced is
        # 'true'. We need the cumul to be > 50_000 already.
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 50000, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    # Now arm session enforcement and try to add 1 more. Expected : EXCEPTION.
    def _insert_enforced_global():
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL app.caps_enforced = 'true'"))
            user2 = _seed_user(conn)
            conn.execute(
                text(
                    "INSERT INTO cabecoin_transactions "
                    "(user_id, direction, amount, reason) "
                    "VALUES (:u, 'credit', 1, 'admin_adjustment')"
                ),
                {"u": user2},
            )

    with pytest.raises((InternalError, ProgrammingError)) as exc_info:
        _insert_enforced_global()
    assert "global daily block" in str(exc_info.value).lower()


def test_caps_active_per_user_block_at_5k(engine):
    """Both switches armed : same user accumulates 5_000 then tries 1
    more → per-user EXCEPTION."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app_settings "
                "SET data = jsonb_set(data, '{caps_enforced}', 'true'::jsonb) "
                "WHERE section = 'db_pipeline_caps'"
            )
        )
    # Seed the user and pre-fill 5_000 without enforcement (session not armed).
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 5000, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    # Arm session enforcement and try to add 1 more for the same user.
    def _insert_enforced_per_user():
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL app.caps_enforced = 'true'"))
            conn.execute(
                text(
                    "INSERT INTO cabecoin_transactions "
                    "(user_id, direction, amount, reason) "
                    "VALUES (:u, 'credit', 1, 'admin_adjustment')"
                ),
                {"u": user_id},
            )

    with pytest.raises((InternalError, ProgrammingError)) as exc_info:
        _insert_enforced_per_user()
    assert "per-user daily block" in str(exc_info.value).lower()


def test_caps_active_warn_above_20k_no_rollback(engine):
    """Both switches armed : cumul reaches 20_001 → WARNING only, no rollback."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app_settings "
                "SET data = jsonb_set(data, '{caps_enforced}', 'true'::jsonb) "
                "WHERE section = 'db_pipeline_caps'"
            )
        )
    # Pre-fill 20_000 without enforcement.
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 20000, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    # Push 1 more under a different user with enforcement on. Should NOT
    # raise — warn only — and the row must be persisted.
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL app.caps_enforced = 'true'"))
        user2 = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 1, 'admin_adjustment')"
            ),
            {"u": user2},
        )

    with engine.connect() as conn:
        total = conn.execute(text("SELECT SUM(amount) FROM cabecoin_transactions WHERE direction = 'credit'")).scalar()
        assert total == 20001


def test_caps_direction_aware_debits_not_counted(engine):
    """A ``direction = 'debit'`` insert does not contribute to the cumul.
    Even with both switches armed, a 1_000_000 debit slides through."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app_settings "
                "SET data = jsonb_set(data, '{caps_enforced}', 'true'::jsonb) "
                "WHERE section = 'db_pipeline_caps'"
            )
        )
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL app.caps_enforced = 'true'"))
        user_id = _seed_user(conn)
        # Debit ≥ block threshold — must not raise (direction-aware).
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'debit', 1000000, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT direction, amount FROM cabecoin_transactions WHERE user_id = :u"),
            {"u": user_id},
        ).first()
        assert row == ("debit", 1000000)


def test_caps_session_not_armed_settings_armed_is_dormant(engine):
    """Settings armed, but session NOT armed → no-op. Mirror : session
    armed but settings not armed → no-op. Both required (double switch)."""
    # Arm settings only.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app_settings "
                "SET data = jsonb_set(data, '{caps_enforced}', 'true'::jsonb) "
                "WHERE section = 'db_pipeline_caps'"
            )
        )
    # 1_000_000 without SET LOCAL → must pass (no-op despite settings).
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 1000000, 'admin_adjustment')"
            ),
            {"u": user_id},
        )
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM cabecoin_transactions WHERE direction = 'credit'")).scalar()
        assert n == 1


def test_app_settings_db_pipeline_caps_seeded(engine):
    """Section is seeded by the migration with the expected default
    values from the brainstorm (cf spec §3)."""
    with engine.connect() as conn:
        row = conn.execute(text("SELECT data FROM app_settings WHERE section = 'db_pipeline_caps'")).first()
        assert row is not None
        data = row[0]
        # caps_enforced defaults to false (dormant).
        assert data["caps_enforced"] is False
        assert int(data["cab_global_daily_warn"]) == 20000
        assert int(data["cab_global_daily_block"]) == 50000
        assert int(data["cab_per_user_daily_block"]) == 5000


def test_cabecoin_transactions_user_created_index_exists(engine):
    """The migration adds ``ix_cabecoin_tx_user_created`` so the trigger's
    per-user SUM is bounded by an index lookup."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'cabecoin_transactions'")
        ).all()
        names = {r[0] for r in rows}
        assert "ix_cabecoin_tx_user_created" in names
