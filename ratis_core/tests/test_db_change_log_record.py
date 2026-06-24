"""Tests for the generic change-log trigger ``fn_db_change_log_record()``.

HSP2 — the trigger fires AFTER INSERT/UPDATE/DELETE on the 6 sensitive
tables and inserts one row in ``db_change_log`` per row touched. The
``submission_id`` column is read from the transaction-local setting
``app.submission_id`` (NULL if unset).

We use the alembic-spinup fixture (DB jetable) because the trigger is
created by the migration, not by ``Base.metadata.create_all()``.

The 6 sensitive tables are (cf spec §2) :
    user_cab_balance, cabecoin_transactions, cashback_transactions,
    cashback_withdrawals, subscriptions, scans
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def hsp2_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp2_record")


@pytest.fixture
def engine(hsp2_db_url: str) -> Iterator[Engine]:
    eng = create_engine(hsp2_db_url)
    try:
        with eng.begin() as conn:
            conn.execute(text("TRUNCATE TABLE db_change_log"))
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


def test_record_on_cabecoin_transactions_insert_no_submission_id(engine):
    """INSERT without ``SET LOCAL app.submission_id`` → row in
    db_change_log with submission_id NULL."""
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'credit', 100, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT submission_id, table_name, op, "
                "       new_data->>'amount' AS amount "
                "FROM db_change_log WHERE table_name = 'cabecoin_transactions'"
            )
        ).all()
        assert len(rows) == 1
        sub_id, table_name, op_kind, amount = rows[0]
        assert sub_id is None
        assert table_name == "cabecoin_transactions"
        assert op_kind == "insert"
        assert amount == "100"


def test_record_on_cabecoin_transactions_insert_with_submission_id(engine):
    """``SET LOCAL app.submission_id = '<uuid>'`` → submission_id captured."""
    submission_id = uuid.uuid4()
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(text(f"SET LOCAL app.submission_id = '{submission_id}'"))
        conn.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(user_id, direction, amount, reason) "
                "VALUES (:u, 'debit', 50, 'admin_adjustment')"
            ),
            {"u": user_id},
        )

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT submission_id FROM db_change_log WHERE table_name = 'cabecoin_transactions'")
        ).first()
        assert row is not None
        assert str(row[0]) == str(submission_id)


def test_record_on_user_cab_balance_update_captures_old_and_new(engine):
    """UPDATE on user_cab_balance → both old_data and new_data captured."""
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text("INSERT INTO user_cab_balance (user_id, balance) VALUES (:u, 500)"),
            {"u": user_id},
        )
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE db_change_log"))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE user_cab_balance SET balance = balance + 200 WHERE user_id = :u"),
            {"u": user_id},
        )

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT op, "
                "       (old_data->>'balance')::int AS old_bal, "
                "       (new_data->>'balance')::int AS new_bal "
                "FROM db_change_log WHERE table_name = 'user_cab_balance'"
            )
        ).first()
        assert row is not None
        op_kind, old_bal, new_bal = row
        assert op_kind == "update"
        assert old_bal == 500
        assert new_bal == 700


def test_record_on_scans_delete_captures_old_data(engine):
    """DELETE on scans → old_data captured, new_data NULL.

    Column is ``scan_type`` (not ``type``) — confirmed on migrated DB.
    Uses scan_type='electronic_label' (no product_ean FK required, no
    receipt_id required) and store_status='unknown' with store_id=NULL
    (satisfies ck_scans_store_status_consistency).
    price=0 satisfies price_pos (>= 0). — R33: constraints from migrated DB.
    """
    scan_id = uuid.uuid4()
    with engine.begin() as conn:
        user_id = _seed_user(conn)
        conn.execute(
            text(
                "INSERT INTO scans "
                "(id, user_id, scan_type, status, price, store_status) "
                "VALUES (:s, :u, 'electronic_label', 'pending', 0, 'unknown')"
            ),
            {"s": scan_id, "u": user_id},
        )
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE db_change_log"))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM scans WHERE id = :s"), {"s": scan_id})

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT op, new_data, old_data->>'id' AS old_id FROM db_change_log WHERE table_name = 'scans'")
        ).first()
        assert row is not None
        op_kind, new_data, old_id = row
        assert op_kind == "delete"
        assert new_data is None
        assert old_id == str(scan_id)


@pytest.mark.parametrize(
    "table_name",
    [
        "user_cab_balance",
        "cabecoin_transactions",
        "cashback_transactions",
        "cashback_withdrawals",
        "subscriptions",
        "scans",
    ],
)
def test_all_six_sensitive_tables_have_record_trigger(engine, table_name: str):
    """``trg_db_change_log_record_<table>`` is attached on each of the 6
    sensitive tables. Names per spec §2 / Section 2."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE c.relname = :t AND NOT t.tgisinternal"
            ),
            {"t": table_name},
        ).all()
        names = {r[0] for r in rows}
        expected = f"trg_db_change_log_record_{table_name}"
        assert expected in names, f"missing trigger on {table_name} (got {names})"
