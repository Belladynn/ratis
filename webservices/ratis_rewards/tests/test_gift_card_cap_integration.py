"""
TDD — fiscal cap integration in issue_gift_card (audit H4, Task 3).

Tests that ``issue_gift_card`` calls ``reserve_gift_card_cap`` BEFORE the Runa
HTTP call and reacts correctly to each CapDecision outcome:

  allow  → Runa is called, order ends 'issued', cap_reserved_cents set, ytd bumped.
  block  → Runa NOT called, order ends 'failed', CAB refunded (shop_purchase).
  defer  → Runa NOT called, order stays 'pending', eligible_at set to Jan 1.
  idempotent → re-issuing an already-reserved order does NOT double-bump ytd.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from services.gift_card_service import issue_gift_card
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_gift_card_order, make_user

UTC = UTC
CAP = 119900  # boutique.cap_annual_cents — must match ratis_settings.json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runa_ok(code: str = "GC-AABB-1234") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"id": "runa_ord_001", "status": "COMPLETE", "redemption_code": code}
    return resp


def _get_ytd(db, user_id) -> int:
    row = db.execute(
        text("SELECT gift_card_redeemed_ytd_cents FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).first()
    return int(row.gift_card_redeemed_ytd_cents)


def _get_cap_reserved(db, order_id) -> int:
    row = db.execute(
        text("SELECT cap_reserved_cents FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).first()
    return int(row.cap_reserved_cents)


def _get_order_status(db, order_id) -> str:
    return db.execute(text("SELECT status FROM gift_card_orders WHERE id = :id"), {"id": order_id}).scalar()


def _get_eligible_at(db, order_id):
    return db.execute(text("SELECT eligible_at FROM gift_card_orders WHERE id = :id"), {"id": order_id}).scalar()


def _set_ytd(db, user_id, cents: int) -> None:
    db.execute(
        text("UPDATE users SET gift_card_redeemed_ytd_cents = :c WHERE id = :uid"),
        {"c": cents, "uid": user_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# H4-a: shop_purchase, user under cap → Runa called, order issued, ytd bumped
# ---------------------------------------------------------------------------


def test_issue_gift_card_reserves_cap_allow(db):
    """shop_purchase, user under cap → Runa called, order 'issued',
    cap_reserved_cents == denomination, ytd bumped by denomination."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=5000,
        status="pending",
        source_type="shop_purchase",
    )

    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = _runa_ok()
        issue_gift_card(order_id, db)
        db.commit()

    assert mock_post.called, "Runa must be called when cap allows"
    assert _get_order_status(db, order_id) == "issued"
    assert _get_cap_reserved(db, order_id) == 5000
    assert _get_ytd(db, user_id) == 5000


# ---------------------------------------------------------------------------
# H4-b: shop_purchase, user at cap → Runa NOT called, order 'failed', CAB refunded
# ---------------------------------------------------------------------------


def test_issue_gift_card_blocks_at_cap_shop_purchase(db):
    """shop_purchase, user already at cap → Runa NOT called, order 'failed', CAB refunded."""
    user_id = make_user(db)
    debit_tx_id = uuid.uuid4()
    denomination = 3000

    # Seed the user at the cap (ytd == CAP)
    _set_ytd(db, user_id, CAP)

    # Seed CAB balance + debit transaction (mirrors boutique flow)
    db.execute(
        text("UPDATE user_cab_balance SET balance = :bal WHERE user_id = :uid"),
        {"bal": denomination, "uid": user_id},
    )
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

    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=denomination,
        status="pending",
        source_type="shop_purchase",
        source_ref_id=str(debit_tx_id),
    )

    with patch("services.gift_card_service.httpx.post") as mock_post:
        issue_gift_card(order_id, db)
        db.commit()

    assert not mock_post.called, "Runa must NOT be called when cap is exceeded (shop_purchase)"
    assert _get_order_status(db, order_id) == "failed"

    # CAB must be refunded (balance restored to denomination)
    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == denomination

    # ytd unchanged (no reservation happened)
    assert _get_ytd(db, user_id) == CAP


# ---------------------------------------------------------------------------
# H4-c: annual_subscription at cap → Runa NOT called, order stays 'pending',
#         eligible_at set to Jan 1 next year, ytd unchanged
# ---------------------------------------------------------------------------


def test_issue_gift_card_defers_at_cap_subscription(db):
    """annual_subscription over cap → Runa NOT called, order stays 'pending',
    eligible_at = Jan 1 of next year, ytd unchanged."""
    user_id = make_user(db)

    # Seed user at cap
    _set_ytd(db, user_id, CAP)

    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=5000,
        status="pending",
        source_type="annual_subscription",
    )

    with patch("services.gift_card_service.httpx.post") as mock_post:
        issue_gift_card(order_id, db)
        db.commit()

    assert not mock_post.called, "Runa must NOT be called when deferred"
    assert _get_order_status(db, order_id) == "pending"

    eligible_at = _get_eligible_at(db, order_id)
    assert eligible_at is not None, "eligible_at must be set on deferral"
    expected_year = datetime.now(UTC).year + 1
    # eligible_at is stored in DB with timezone; compare year/month/day
    assert eligible_at.year == expected_year
    assert eligible_at.month == 1
    assert eligible_at.day == 1

    assert _get_ytd(db, user_id) == CAP  # unchanged


# ---------------------------------------------------------------------------
# H4-d: re-issuing an already-reserved order does NOT double-bump ytd
# ---------------------------------------------------------------------------


def test_issue_gift_card_idempotent_cap_reservation(db):
    """Calling issue_gift_card twice on the same order does not double-bump ytd.

    The second call hits the idempotence gate (status == 'issued') and returns
    early — reserve_gift_card_cap is not reached a second time. Even if it were
    called again, the cap_reserved_cents > 0 idempotency guard in the service
    would prevent double-counting.
    """
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=4000,
        status="pending",
        source_type="shop_purchase",
    )

    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = _runa_ok()
        # First call — issues successfully
        issue_gift_card(order_id, db)
        db.commit()

    assert _get_order_status(db, order_id) == "issued"
    assert _get_ytd(db, user_id) == 4000
    assert _get_cap_reserved(db, order_id) == 4000

    with patch("services.gift_card_service.httpx.post") as mock_post2:
        mock_post2.return_value = _runa_ok()
        # Second call — idempotence gate returns early
        issue_gift_card(order_id, db)
        db.commit()

    # ytd must NOT be doubled
    assert _get_ytd(db, user_id) == 4000
    assert _get_cap_reserved(db, order_id) == 4000
    # Runa must NOT be called again
    assert not mock_post2.called


# ---------------------------------------------------------------------------
# H4-e: _mark_failed on a reserved order releases the cap (Task 4)
# ---------------------------------------------------------------------------


def test_mark_failed_releases_reserved_cap(db):
    """A reserved shop_purchase order put through _mark_failed:
    - order status becomes 'failed'
    - CAB is refunded (existing C3 behaviour)
    - gift_card_redeemed_ytd_cents is decremented back to pre-reserve value
    - cap_reserved_cents is zeroed
    """
    from services.gift_card_cap_service import reserve_gift_card_cap
    from services.gift_card_service import _mark_failed

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    debit_tx_id = uuid.uuid4()
    denomination = 5000

    # Seed CAB debit (mirrors boutique flow)
    db.execute(
        text("UPDATE user_cab_balance SET balance = :bal WHERE user_id = :uid"),
        {"bal": denomination, "uid": user_id},
    )
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

    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=denomination,
        status="pending",
        source_type="shop_purchase",
        source_ref_id=str(debit_tx_id),
    )

    # Reserve the cap (simulates what issue_gift_card does before the Runa call)
    decision = reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()
    assert decision.outcome == "allow"
    assert _get_ytd(db, user_id) == denomination
    assert _get_cap_reserved(db, order_id) == denomination

    # Now the Runa call fails — _mark_failed is called
    _mark_failed(db, order_id)
    db.commit()

    # Order must be 'failed'
    assert _get_order_status(db, order_id) == "failed"

    # CAB must be refunded
    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == denomination

    # Fiscal cap must be released
    assert _get_ytd(db, user_id) == 0, "ytd must be decremented back to pre-reserve value"
    assert _get_cap_reserved(db, order_id) == 0, "cap_reserved_cents must be zeroed"


# ---------------------------------------------------------------------------
# H4-f: _mark_failed on a never-reserved order does NOT underflow ytd (Task 4)
# ---------------------------------------------------------------------------


def test_mark_failed_never_reserved_no_ytd_change(db):
    """A never-reserved order (cap_reserved_cents == 0) put through _mark_failed:
    - gift_card_redeemed_ytd_cents is NOT decremented (no spurious underflow)
    """
    from services.gift_card_service import _mark_failed

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)

    # Set a known ytd value so we can verify no change
    initial_ytd = 3000
    db.execute(
        text("UPDATE users SET gift_card_redeemed_ytd_cents = :c WHERE id = :uid"),
        {"c": initial_ytd, "uid": user_id},
    )
    db.commit()

    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=2000,
        status="pending",
        source_type="annual_subscription",
    )
    # cap_reserved_cents is 0 by default (never reserved)

    _mark_failed(db, order_id)
    db.commit()

    assert _get_order_status(db, order_id) == "failed"
    # ytd must be unchanged
    assert _get_ytd(db, user_id) == initial_ytd, "ytd must NOT change for never-reserved orders"


# ---------------------------------------------------------------------------
# H4-g (optional): end-to-end issue_gift_card where Runa returns FAILED
#                  after a successful cap reservation → order failed, ytd released,
#                  CAB refunded (Task 4)
# ---------------------------------------------------------------------------


def test_issue_gift_card_runa_failed_releases_cap(db):
    """End-to-end: Runa returns HTTP 402 FAILED after cap reservation.
    - order status becomes 'failed'
    - CAB is refunded
    - gift_card_redeemed_ytd_cents is released back to 0
    - cap_reserved_cents is zeroed
    """
    from unittest.mock import MagicMock

    import httpx

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    debit_tx_id = uuid.uuid4()
    denomination = 4000

    # Seed CAB debit
    db.execute(
        text("UPDATE user_cab_balance SET balance = :bal WHERE user_id = :uid"),
        {"bal": denomination, "uid": user_id},
    )
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

    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=denomination,
        status="pending",
        source_type="shop_purchase",
        source_ref_id=str(debit_tx_id),
    )

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

    assert _get_order_status(db, order_id) == "failed"

    # CAB refunded
    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == denomination

    # Fiscal cap released
    assert _get_ytd(db, user_id) == 0, "ytd must be released after Runa FAILED"
    assert _get_cap_reserved(db, order_id) == 0, "cap_reserved_cents must be zeroed"
