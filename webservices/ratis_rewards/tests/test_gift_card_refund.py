"""
TDD — CAB refund on gift-card issuance failure (audit C3, Part B).

Tests the _mark_failed + _refund_order_cab service path:
- failed order → CAB refunded (exact amount, gift_card_refund reason)
- idempotency: second _mark_failed call does NOT double-refund
- non-shop_purchase orders do NOT trigger a CAB refund
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from services.gift_card_service import _mark_failed, issue_gift_card
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_gift_card_order, make_user


def _seed_shop_purchase_order(db, *, denomination: int = 3000) -> tuple[uuid.UUID, uuid.UUID, int]:
    """Seed a user + shop_purchase order with a matching debit transaction.

    Returns (user_id, order_id, debited_amount).
    The debit cabecoin_transactions row uses str(debit_tx_id) as the order's
    source_ref_id (mirrors the boutique flow in create_order).
    """
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    debit_tx_id = uuid.uuid4()

    # Set the CAB balance high enough to absorb the debit
    db.execute(
        text("UPDATE user_cab_balance SET balance = :bal WHERE user_id = :uid"),
        {"bal": denomination, "uid": user_id},
    )
    # Insert a debit transaction (mirrors boutique_service.create_order)
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "    (id, user_id, direction, amount, reason) "
            "VALUES (:id, :uid, 'debit', :amount, 'gift_card_purchase')"
        ),
        {"id": debit_tx_id, "uid": user_id, "amount": denomination},
    )
    db.execute(
        text("UPDATE user_cab_balance SET balance = balance - :amount WHERE user_id = :uid"),
        {"amount": denomination, "uid": user_id},
    )
    db.commit()

    # Insert the gift_card_order with source_ref_id = str(debit_tx_id)
    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=denomination,
        status="pending",
        source_type="shop_purchase",
        source_ref_id=str(debit_tx_id),
    )

    return user_id, order_id, denomination


# ---------------------------------------------------------------------------
# B3-a: failed gift-card order refunds CAB
# ---------------------------------------------------------------------------


def test_failed_gift_card_order_refunds_cab(db):
    """_mark_failed on a shop_purchase order → status failed + CAB refunded.

    Balance is restored to pre-purchase level, and a cabecoin_transactions
    credit row with reason 'gift_card_refund' is inserted for the exact
    debited amount.
    """
    user_id, order_id, debit_amount = _seed_shop_purchase_order(db, denomination=3000)

    # Pre-conditions: balance is zero (all CAB debited)
    bal_before = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal_before == 0

    _mark_failed(db, order_id)
    db.commit()

    # Order status should be 'failed'
    row = db.execute(
        text("SELECT status, failed_at FROM gift_card_orders WHERE id = :id"),
        {"id": order_id},
    ).first()
    assert row.status == "failed"
    assert row.failed_at is not None

    # CAB balance must be back to pre-purchase level (3000)
    bal_after = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal_after == debit_amount

    # A credit row with reason 'gift_card_refund' must exist
    credit = db.execute(
        text(
            "SELECT amount, direction, reason "
            "FROM cabecoin_transactions "
            "WHERE user_id = :uid AND direction = 'credit' AND reason = 'gift_card_refund'"
        ),
        {"uid": user_id},
    ).first()
    assert credit is not None
    assert credit.amount == debit_amount
    assert credit.reason == "gift_card_refund"


# ---------------------------------------------------------------------------
# B3-b: idempotency — second _mark_failed call does NOT double-refund
# ---------------------------------------------------------------------------


def test_refund_is_idempotent(db):
    """Calling _mark_failed twice does NOT double-refund.

    The first call transitions status → 'failed' and refunds CAB.
    The second call: update_order_failed returns False (status != 'pending')
    so _refund_order_cab is never entered.
    """
    user_id, order_id, debit_amount = _seed_shop_purchase_order(db, denomination=2000)

    _mark_failed(db, order_id)
    db.commit()

    # Second call — must be a no-op
    _mark_failed(db, order_id)
    db.commit()

    # Balance should be debit_amount, not 2 * debit_amount
    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == debit_amount

    # Only one credit row
    credit_count = db.execute(
        text(
            "SELECT COUNT(*) FROM cabecoin_transactions "
            "WHERE user_id = :uid AND direction = 'credit' AND reason = 'gift_card_refund'"
        ),
        {"uid": user_id},
    ).scalar()
    assert credit_count == 1


# ---------------------------------------------------------------------------
# B3-c: non-shop_purchase order does NOT trigger a CAB refund
# ---------------------------------------------------------------------------


def test_refund_skipped_for_non_shop_purchase(db):
    """A non-shop_purchase order failing does NOT credit CAB.

    Annual subscription / battlepass / referral gift cards are NOT purchased
    with CAB; there is nothing to refund.
    """
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)

    # Give the user some CAB so we can distinguish "no refund" from "already zero"
    db.execute(
        text("UPDATE user_cab_balance SET balance = 500 WHERE user_id = :uid"),
        {"uid": user_id},
    )
    db.commit()

    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=2000,
        status="pending",
        source_type="annual_subscription",
        source_ref_id=str(uuid.uuid4()),
    )

    _mark_failed(db, order_id)
    db.commit()

    # Order must be 'failed'
    status = db.execute(text("SELECT status FROM gift_card_orders WHERE id = :id"), {"id": order_id}).scalar()
    assert status == "failed"

    # Balance must be UNCHANGED (500 — no refund)
    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 500

    # No gift_card_refund credit row
    credit_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'gift_card_refund'"),
        {"uid": user_id},
    ).scalar()
    assert credit_count == 0


# ---------------------------------------------------------------------------
# B3-d: _mark_failed rollback — award_cab raises → order stays pending, no credit
# ---------------------------------------------------------------------------


def test_mark_failed_rollback_on_award_cab_error(db):
    """When _refund_order_cab's award_cab raises, _mark_failed rolls back.

    The exception inside _mark_failed is caught and suppressed (it never
    re-raises). After the call:
      (a) no exception escapes _mark_failed
      (b) the order status is still 'pending' (update_order_failed was rolled back)
      (c) the user's CAB balance is unchanged
      (d) no 'gift_card_refund' credit row exists
    """
    user_id, order_id, _denomination = _seed_shop_purchase_order(db, denomination=1800)

    bal_before = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal_before == 0  # all CAB was debited during seeding

    with patch(
        "services.gift_card_service.award_cab",
        side_effect=RuntimeError("simulated award_cab failure"),
    ):
        # (a) must NOT raise
        _mark_failed(db, order_id)

    # (b) order must still be 'pending' — the UPDATE was rolled back
    status = db.execute(
        text("SELECT status FROM gift_card_orders WHERE id = :id"),
        {"id": order_id},
    ).scalar()
    assert status == "pending"

    # (c) CAB balance unchanged
    bal_after = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal_after == bal_before

    # (d) no gift_card_refund credit row
    credit_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'gift_card_refund'"),
        {"uid": user_id},
    ).scalar()
    assert credit_count == 0


# ---------------------------------------------------------------------------
# Integration: Runa FAILED → full flow via issue_gift_card
# ---------------------------------------------------------------------------


def test_issue_gift_card_runa_failed_refunds_cab(db):
    """End-to-end: Runa returns FAILED → order failed AND CAB is refunded."""
    import httpx

    user_id, order_id, debit_amount = _seed_shop_purchase_order(db, denomination=1500)

    mock_resp = MagicMock()
    mock_resp.status_code = 402
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "402",
        request=MagicMock(),
        response=mock_resp,
    )

    with patch("services.gift_card_service.httpx.post", return_value=mock_resp):
        issue_gift_card(order_id, db)
        db.commit()

    row = db.execute(text("SELECT status FROM gift_card_orders WHERE id = :id"), {"id": order_id}).first()
    assert row.status == "failed"

    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == debit_amount
