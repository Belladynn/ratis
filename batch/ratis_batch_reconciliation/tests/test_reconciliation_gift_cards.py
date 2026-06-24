# batch/ratis_batch_reconciliation/tests/test_reconciliation_gift_cards.py
"""TDD — reconcile_pending_gift_card_orders (audit C3, Part C).

Covers:
- A pending shop_purchase order older than 24h → failed + CAB refunded.
- An order younger than 24h → left alone.
- dry_run=True → no writes.
- A non-shop_purchase pending order → ignored.
- Idempotency: second run finds nothing.
- H4: a stuck order with cap_reserved_cents > 0 → cap released on reconciliation.
"""

from __future__ import annotations

import uuid

from reconciliation.gift_cards import reconcile_pending_gift_card_orders
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_gift_card_brand(session_factory) -> uuid.UUID:
    """Insert a minimal gift_card_brands row."""
    brand_id = uuid.uuid4()
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO gift_card_brands "
                "    (id, name, provider_brand_id, is_active, created_at) "
                "VALUES (:id, :name, :pbid, true, now())"
            ),
            {
                "id": brand_id,
                "name": f"Brand {brand_id.hex[:6]}",
                "pbid": f"runa_{brand_id.hex[:6]}",
            },
        )
        db.commit()
    return brand_id


def _seed_shop_purchase_order(
    session_factory,
    *,
    denomination: int = 2000,
    hours_old: float = 25.0,
    status: str = "pending",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, int]:
    """Seed a user with a shop_purchase order + matching debit transaction.

    Returns (user_id, order_id, debit_tx_id, denomination).
    The order's source_ref_id is str(debit_tx_id) to mirror the boutique flow.
    """
    brand_id = _insert_gift_card_brand(session_factory)
    user_id = uuid.uuid4()
    debit_tx_id = uuid.uuid4()
    order_id = uuid.uuid4()

    with session_factory() as db:
        from ratis_core.identifiers import generate_support_id

        db.execute(
            text(
                "INSERT INTO users (id, email, support_id, account_type, "
                "                  is_deleted) "
                "VALUES (:id, :email, :sid, 'oauth', false)"
            ),
            {
                "id": user_id,
                "email": f"test_{user_id.hex[:8]}@example.com",
                "sid": generate_support_id(),
            },
        )
        db.execute(
            text("INSERT INTO user_cab_balance (user_id, balance) VALUES (:uid, 0)"),
            {"uid": user_id},
        )
        db.execute(
            text("INSERT INTO user_cashback_balance (user_id, balance) VALUES (:uid, 0)"),
            {"uid": user_id},
        )
        # Insert the debit transaction (CAB was taken at purchase)
        db.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "    (id, user_id, direction, amount, reason) "
                "VALUES (:id, :uid, 'debit', :amount, 'gift_card_purchase')"
            ),
            {"id": debit_tx_id, "uid": user_id, "amount": denomination},
        )
        # Insert the pending gift-card order
        created_offset = f"now() - interval '{int(hours_old * 60)} minutes'"
        db.execute(
            text(
                "INSERT INTO gift_card_orders "
                "    (id, user_id, brand_id, denomination, status, source_type, "
                "     source_ref_id, created_at) "
                f"VALUES (:id, :uid, :bid, :denom, :status, 'shop_purchase', "
                f"        :sref, {created_offset})"
            ),
            {
                "id": order_id,
                "uid": user_id,
                "bid": brand_id,
                "denom": denomination,
                "status": status,
                "sref": str(debit_tx_id),
            },
        )
        db.commit()

    return user_id, order_id, debit_tx_id, denomination


# ---------------------------------------------------------------------------
# C3-a: pending shop_purchase order older than 24h → failed + CAB refunded
# ---------------------------------------------------------------------------


def test_reconcile_stuck_order_failed_and_cab_refunded(session_factory, make_user, assert_no_pending_changes):
    """A shop_purchase order stuck pending > 24h → failed + CAB refunded."""
    user_id, order_id, _debit_tx_id, denomination = _seed_shop_purchase_order(
        session_factory, denomination=2500, hours_old=25.0
    )

    with session_factory() as db:
        count = reconcile_pending_gift_card_orders(db, dry_run=False)

    assert count == 1

    with session_factory() as db:
        row = db.execute(
            text("SELECT status, failed_at FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).first()
        assert row.status == "failed"
        assert row.failed_at is not None

        bal = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar()
        assert bal == denomination  # CAB fully refunded

        credit = db.execute(
            text(
                "SELECT amount, direction, reason FROM cabecoin_transactions "
                "WHERE user_id = :uid AND direction = 'credit' AND reason = 'gift_card_refund'"
            ),
            {"uid": user_id},
        ).first()
        assert credit is not None
        assert credit.amount == denomination


# ---------------------------------------------------------------------------
# C3-b: order younger than 24h → left alone
# ---------------------------------------------------------------------------


def test_reconcile_skips_recent_order(session_factory, make_user, assert_no_pending_changes):
    """A shop_purchase order pending for only 1 hour → untouched."""
    _user_id, order_id, _debit_tx_id, _denomination = _seed_shop_purchase_order(
        session_factory, denomination=1000, hours_old=1.0
    )

    with session_factory() as db:
        count = reconcile_pending_gift_card_orders(db, dry_run=False)

    assert count == 0

    with session_factory() as db:
        status = db.execute(
            text("SELECT status FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).scalar()
        assert status == "pending"  # unchanged


# ---------------------------------------------------------------------------
# C3-c: dry_run=True → no writes
# ---------------------------------------------------------------------------


def test_reconcile_dry_run_no_write(session_factory, make_user, assert_no_pending_changes):
    """dry_run=True → detects the stuck order but writes nothing."""
    user_id, order_id, _debit_tx_id, _denomination = _seed_shop_purchase_order(
        session_factory, denomination=1500, hours_old=30.0
    )

    with session_factory() as db:
        count = reconcile_pending_gift_card_orders(db, dry_run=True)

    assert count == 1

    with session_factory() as db:
        status = db.execute(
            text("SELECT status FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).scalar()
        assert status == "pending"  # nothing written

        credit_count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'gift_card_refund'"),
            {"uid": user_id},
        ).scalar()
        assert credit_count == 0


# ---------------------------------------------------------------------------
# C3-d: non-shop_purchase pending order → ignored
# ---------------------------------------------------------------------------


def test_reconcile_ignores_non_shop_purchase(session_factory, make_user, assert_no_pending_changes):
    """A non-shop_purchase pending order older than 24h → not reconciled."""
    brand_id = _insert_gift_card_brand(session_factory)
    user_id_raw = make_user()
    order_id = uuid.uuid4()

    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO gift_card_orders "
                "    (id, user_id, brand_id, denomination, status, source_type, "
                "     source_ref_id, created_at) "
                "VALUES (:id, :uid, :bid, 2000, 'pending', 'annual_subscription', "
                "        :sref, now() - interval '48 hours')"
            ),
            {
                "id": order_id,
                "uid": user_id_raw,
                "bid": brand_id,
                "sref": str(uuid.uuid4()),
            },
        )
        db.commit()

    with session_factory() as db:
        count = reconcile_pending_gift_card_orders(db, dry_run=False)

    assert count == 0

    with session_factory() as db:
        status = db.execute(
            text("SELECT status FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).scalar()
        assert status == "pending"  # untouched


# ---------------------------------------------------------------------------
# C3-e: idempotency — second run finds nothing
# ---------------------------------------------------------------------------


def test_reconcile_idempotent(session_factory, make_user, assert_no_pending_changes):
    """Running the reconciliation twice: second run finds and reconciles nothing."""
    user_id, _order_id, _debit_tx_id, _denomination = _seed_shop_purchase_order(
        session_factory, denomination=2000, hours_old=26.0
    )

    with session_factory() as db:
        count_1 = reconcile_pending_gift_card_orders(db, dry_run=False)

    assert count_1 == 1

    with session_factory() as db:
        count_2 = reconcile_pending_gift_card_orders(db, dry_run=False)

    assert count_2 == 0

    # Only one credit transaction (no double-refund)
    with session_factory() as db:
        credit_count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'gift_card_refund'"),
            {"uid": user_id},
        ).scalar()
        assert credit_count == 1


# ---------------------------------------------------------------------------
# H4: stuck order with cap_reserved_cents > 0 → cap released on reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_releases_cap_reservation(session_factory, make_user, assert_no_pending_changes):
    """A stuck shop_purchase order that had a cap reservation → cap released.

    Seeds:
    - users.gift_card_redeemed_ytd_cents = 5000 (bumped when order was created)
    - gift_card_orders.cap_reserved_cents = 5000

    After reconcile_pending_gift_card_orders:
    - order status = 'failed'
    - gift_card_orders.cap_reserved_cents = 0
    - users.gift_card_redeemed_ytd_cents decremented back to 0
    """
    brand_id = _insert_gift_card_brand(session_factory)
    user_id = uuid.uuid4()
    debit_tx_id = uuid.uuid4()
    order_id = uuid.uuid4()
    cap_amount = 5000  # 50 €

    with session_factory() as db:
        from ratis_core.identifiers import generate_support_id

        db.execute(
            text(
                "INSERT INTO users (id, email, support_id, account_type, "
                "                  is_deleted, gift_card_redeemed_ytd_cents) "
                "VALUES (:id, :email, :sid, 'oauth', false, :ytd)"
            ),
            {
                "id": user_id,
                "email": f"test_cap_{user_id.hex[:8]}@example.com",
                "sid": generate_support_id(),
                "ytd": cap_amount,
            },
        )
        db.execute(
            text("INSERT INTO user_cab_balance (user_id, balance) VALUES (:uid, 0)"),
            {"uid": user_id},
        )
        db.execute(
            text("INSERT INTO user_cashback_balance (user_id, balance) VALUES (:uid, 0)"),
            {"uid": user_id},
        )
        db.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "    (id, user_id, direction, amount, reason) "
                "VALUES (:id, :uid, 'debit', :amount, 'gift_card_purchase')"
            ),
            {"id": debit_tx_id, "uid": user_id, "amount": cap_amount},
        )
        db.execute(
            text(
                "INSERT INTO gift_card_orders "
                "    (id, user_id, brand_id, denomination, status, source_type, "
                "     source_ref_id, created_at, cap_reserved_cents) "
                "VALUES (:id, :uid, :bid, :denom, 'pending', 'shop_purchase', "
                "        :sref, now() - interval '1800 minutes', :cap)"
            ),
            {
                "id": order_id,
                "uid": user_id,
                "bid": brand_id,
                "denom": cap_amount,
                "sref": str(debit_tx_id),
                "cap": cap_amount,
            },
        )
        db.commit()

    with session_factory() as db:
        count = reconcile_pending_gift_card_orders(db, dry_run=False)

    assert count == 1

    with session_factory() as db:
        order_row = db.execute(
            text("SELECT status, cap_reserved_cents FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).first()
        assert order_row.status == "failed"
        assert order_row.cap_reserved_cents == 0

        ytd = db.execute(
            text("SELECT gift_card_redeemed_ytd_cents FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).scalar()
        assert ytd == 0  # cap_amount decremented back
