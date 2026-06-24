"""Migration tests for ``20260511_1000_au9npfk`` — NEVER PURGE FK + WARN trigger.

Validates :
* FK on the 3 NEVER PURGE tables is ON DELETE SET NULL after upgrade
* ``user_id`` is nullable on the 3 tables after upgrade
* PG trigger ``trg_users_warn_hard_delete`` exists and fires WARNING on
  hard-DELETE of a users row (no block, but RAISE WARNING)
* Hard-DELETE actually SETs user_id to NULL on legally-retained rows
  (vs CASCADE which would delete them)
* Downgrade restores the original CASCADE / RESTRICT actions and drops
  the trigger
* Full roundtrip (upgrade → downgrade → upgrade) is idempotent
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text


TARGET_REVISION = "20260511_1000_au9npfk"
# ``20260511_1000_au9npfk`` is a merge revision (down_revision is a tuple
# of ``obp_opf`` + ``pg_earthdistance``). Downgrading through the merge
# rewinds to BOTH parents — picking either as PREV_REVISION works because
# alembic walks the whole merge boundary. We pick ``obp_opf`` (first
# tuple element).
PREV_REVISION = "20260511_0900_obp_opf"

ALEMBIC_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "alembic.ini"
)


def _unmasked_url(engine) -> str:
    """Return the engine URL with the password un-masked.

    SQLAlchemy ``str(engine.url)`` and ``engine.url.__str__()`` mask the
    password as ``***`` by default, which breaks ``os.environ["DATABASE_URL"]``
    propagation to the Alembic env.py (the masked literal becomes the
    actual password sent to PG → auth failure on local pytest runs). The
    CI uses non-masked env vars so this never showed up there.

    Use ``URL.render_as_string(hide_password=False)`` to get the raw URL.
    """
    return engine.url.render_as_string(hide_password=False)


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", _unmasked_url(engine))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Wipe + run alembic upgrade up to the migration under test."""
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = _unmasked_url(migration_engine)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


def _fk_delete_action(conn, table: str, constraint: str = "fk_user") -> str:
    """Return the ON DELETE action (c=CASCADE, n=SET NULL, r=RESTRICT, a=NO ACTION) for a named FK constraint."""
    row = conn.execute(
        text(
            """
            SELECT confdeltype
            FROM pg_constraint
            WHERE conname = :cname
              AND conrelid = (:tbl)::regclass
            """
        ),
        {"cname": constraint, "tbl": table},
    ).first()
    assert row is not None, f"FK {constraint!r} not found on {table!r}"
    return row.confdeltype


def _col_is_nullable(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    ).first()
    assert row is not None, f"column {table}.{column} not found"
    return row.is_nullable == "YES"


# ---------------------------------------------------------------------------
# Post-upgrade schema invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["subscriptions", "cashback_transactions", "cashback_withdrawals"])
def test_fk_user_is_set_null_after_upgrade(upgraded_engine, table):
    """fk_user must be ON DELETE SET NULL on every NEVER PURGE table."""
    with upgraded_engine.connect() as conn:
        assert _fk_delete_action(conn, table) == "n", (
            f"{table}.fk_user must be ON DELETE SET NULL after migration"
        )


@pytest.mark.parametrize("table", ["subscriptions", "cashback_transactions", "cashback_withdrawals"])
def test_user_id_is_nullable_after_upgrade(upgraded_engine, table):
    """user_id must be nullable so the SET NULL action can succeed."""
    with upgraded_engine.connect() as conn:
        assert _col_is_nullable(conn, table, "user_id"), (
            f"{table}.user_id must be nullable for SET NULL to fire"
        )


def test_warn_trigger_exists(upgraded_engine):
    """The BEFORE DELETE WARN trigger must exist on users."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgrelid = 'users'::regclass
                  AND tgname = 'trg_users_warn_hard_delete'
                  AND NOT tgisinternal
                """
            )
        ).first()
    assert row is not None, "trg_users_warn_hard_delete should exist on users"


# ---------------------------------------------------------------------------
# Behavioural — hard-DELETE on users actually fires the trigger and SETs NULL
# ---------------------------------------------------------------------------


def _make_user(conn, email_suffix: str = "") -> uuid.UUID:
    """Insert a minimal valid users row, satisfying every NOT-NULL invariant."""
    uid = uuid.uuid4()
    sid = uid.hex[:10]
    conn.execute(
        text(
            "INSERT INTO users (id, email, support_id, provider, password_hash, "
            "                  created_at, updated_at, gift_card_redeemed_ytd_cents) "
            "VALUES (:id, :email, :sid, 'email', 'hashed', now(), now(), 0)"
        ),
        {"id": uid, "email": f"u{uid.hex[:8]}{email_suffix}@t.com", "sid": sid},
    )
    return uid


def test_hard_delete_users_sets_user_id_null_on_never_purge_tables(upgraded_engine):
    """Belt-and-braces : hard-DELETE on users SET NULL, never wipes the row.

    Inserts one row per NEVER PURGE table tied to a fresh user, then
    DELETE the user. Each row must (a) still exist, (b) have user_id NULL.
    """
    with upgraded_engine.connect() as conn:
        uid = _make_user(conn, "_delfk")

        # subscription
        sub_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO subscriptions "
                "(id, user_id, status, price, paid_with, payment_ref, plan, "
                " started_at, expires_at) "
                "VALUES (:id, :uid, 'active', 11.99, 'stripe', 'pi_test', 'monthly', "
                "        :start, :exp)"
            ),
            {
                "id": sub_id,
                "uid": uid,
                "start": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(days=30),
            },
        )

        # cashback_transaction — type WITHDRAWAL bypasses the
        # ``credit_requires_offer`` CHECK without needing an affiliate_offer
        ctx_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO cashback_transactions "
                "(id, user_id, type, amount, status, created_at) "
                "VALUES (:id, :uid, 'WITHDRAWAL', 500, 'pending', now())"
            ),
            {"id": ctx_id, "uid": uid},
        )

        # cashback_withdrawal — references the ctx above
        wd_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO cashback_withdrawals "
                "(id, user_id, amount, status, requested_at, updated_at, "
                " cashback_transaction_id) "
                "VALUES (:id, :uid, 500, 'pending', now(), now(), :ctx)"
            ),
            {"id": wd_id, "uid": uid, "ctx": ctx_id},
        )
        conn.commit()

        # Hard-DELETE the user (trigger fires WARNING but does not block).
        # We do NOT assert the WARNING content here because RAISE WARNING
        # surfaces via libpq's notice handler — test_warn_trigger_fires
        # below covers that side-channel via raw asyncpg.
        conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        conn.commit()

        # All 3 rows still exist, user_id is NULL.
        for table, row_id in [
            ("subscriptions", sub_id),
            ("cashback_transactions", ctx_id),
            ("cashback_withdrawals", wd_id),
        ]:
            row = conn.execute(
                text(f"SELECT user_id FROM {table} WHERE id = :id"),
                {"id": row_id},
            ).first()
            assert row is not None, f"{table} row was wrongly deleted"
            assert row.user_id is None, (
                f"{table}.user_id should be NULL after user hard-DELETE, got {row.user_id!r}"
            )


def test_warn_trigger_fires_on_hard_delete(upgraded_engine):
    """Hard-DELETE on users must emit a NOTICE/WARNING via libpq.

    psycopg surfaces RAISE WARNING through the connection's ``info.notices``
    list (and the underlying ``pq.PGconn.notice_handler``). We tap into the
    raw psycopg connection to assert the warning message includes the
    expected guardrail wording.
    """
    with upgraded_engine.connect() as conn:
        uid = _make_user(conn, "_warn")
        conn.commit()

        raw = conn.connection.dbapi_connection
        captured: list[str] = []

        def _handler(diag):  # psycopg notice diagnostic handler
            captured.append(diag.message_primary or "")

        raw.add_notice_handler(_handler)
        try:
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
            conn.commit()
        finally:
            # psycopg has no remove_notice_handler — handler garbage-collects
            # when the connection closes. Best-effort cleanup.
            pass

    assert any("Hard DELETE on users.id=" in m for m in captured), (
        "Expected RAISE WARNING from trg_users_warn_hard_delete — got: %r" % captured
    )
    assert any("NEVER PURGE" in m for m in captured), (
        "Warning should mention NEVER PURGE invariant — got: %r" % captured
    )


# ---------------------------------------------------------------------------
# Downgrade roundtrip
# ---------------------------------------------------------------------------


def test_downgrade_then_upgrade_roundtrip(upgraded_engine):
    """downgrade → upgrade : the couple must be idempotent.

    Runs last in the module (alphabetically after the others) — keep it
    after the behavioural tests so they see the upgraded schema.

    Preconditions reset : the earlier behavioural tests intentionally
    leave rows with ``user_id IS NULL`` on the 3 NEVER PURGE tables (the
    whole point of SET NULL). The migration downgrade restores
    ``user_id NOT NULL`` which would fail with a NotNullViolation against
    those orphan rows. That is by design (see migration docstring), but
    for the roundtrip-idempotency test we need to clean those rows first
    so we exercise the structural-DDL path only.
    """
    with upgraded_engine.connect() as conn:
        # Order matters : cashback_withdrawals → cashback_transactions
        # (FK RESTRICT chain) → subscriptions.
        for tbl in ("cashback_withdrawals", "cashback_transactions", "subscriptions"):
            conn.execute(text(f"DELETE FROM {tbl} WHERE user_id IS NULL"))
        conn.commit()

    cfg = _make_alembic_config(upgraded_engine)
    command.downgrade(cfg, PREV_REVISION)

    # After downgrade, the WARN trigger must be gone and FKs restored to
    # their original ON DELETE actions.
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgrelid = 'users'::regclass "
                "  AND tgname = 'trg_users_warn_hard_delete' "
                "  AND NOT tgisinternal"
            )
        ).first()
        assert row is None, "WARN trigger should be dropped on downgrade"

        # FKs restored — see migration docstring for the original actions.
        assert _fk_delete_action(conn, "subscriptions") == "c", "subscriptions should revert to CASCADE"
        assert _fk_delete_action(conn, "cashback_transactions") == "c", "cashback_transactions should revert to CASCADE"
        assert _fk_delete_action(conn, "cashback_withdrawals") == "r", "cashback_withdrawals should revert to RESTRICT"

        # user_id back to NOT NULL on all 3.
        for tbl in ("subscriptions", "cashback_transactions", "cashback_withdrawals"):
            assert not _col_is_nullable(conn, tbl, "user_id"), (
                f"{tbl}.user_id should be NOT NULL after downgrade"
            )

    # Re-upgrade
    command.upgrade(cfg, TARGET_REVISION)
    with upgraded_engine.connect() as conn:
        for tbl in ("subscriptions", "cashback_transactions", "cashback_withdrawals"):
            assert _fk_delete_action(conn, tbl) == "n", f"{tbl} should be SET NULL again"
            assert _col_is_nullable(conn, tbl, "user_id"), f"{tbl}.user_id should be nullable again"
