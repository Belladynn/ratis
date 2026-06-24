"""TDD — gift_card_cap_service (audit H4).

Tests the central DAS2 fiscal-cap reservation service:
- reserve_gift_card_cap: reserves an order's denomination against the user's annual cap
- release_gift_card_cap: releases the reservation on failure
- next_jan_1_utc: returns the first of January of the following year in UTC

Cap value: 119900 cents (1199 €), from boutique.cap_annual_cents in ratis_settings.json.
"""

from __future__ import annotations

from datetime import UTC, datetime

from services.gift_card_cap_service import (
    next_jan_1_utc,
    release_gift_card_cap,
    reserve_gift_card_cap,
)
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_gift_card_order, make_user

UTC = UTC
CAP = 119900  # boutique.cap_annual_cents


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


def _set_ytd(db, user_id, cents: int) -> None:
    db.execute(
        text("UPDATE users SET gift_card_redeemed_ytd_cents = :c WHERE id = :uid"),
        {"c": cents, "uid": user_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# reserve — happy path (under cap)
# ---------------------------------------------------------------------------


def test_reserve_under_cap_returns_allow(db):
    """reserve under cap → outcome='allow'; ytd incremented; cap_reserved_cents set."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    decision = reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()

    assert decision.outcome == "allow"
    assert decision.deferred_until is None
    assert _get_ytd(db, user_id) == 2000
    assert _get_cap_reserved(db, order_id) == 2000


# ---------------------------------------------------------------------------
# reserve — over cap with allow_defer=True → defer
# ---------------------------------------------------------------------------


def test_reserve_over_cap_defer(db):
    """reserve over cap with allow_defer=True → outcome='defer', ytd unchanged, cap_reserved stays 0."""
    user_id = make_user(db)
    _set_ytd(db, user_id, CAP - 1)  # 1 cent below cap → any positive denom overflows

    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    decision = reserve_gift_card_cap(db, order_id, allow_defer=True)
    db.commit()

    assert decision.outcome == "defer"
    assert decision.deferred_until is not None
    expected = datetime(datetime.now(UTC).year + 1, 1, 1, tzinfo=UTC)
    assert decision.deferred_until == expected
    assert _get_ytd(db, user_id) == CAP - 1  # unchanged
    assert _get_cap_reserved(db, order_id) == 0


# ---------------------------------------------------------------------------
# reserve — over cap with allow_defer=False → block
# ---------------------------------------------------------------------------


def test_reserve_over_cap_block(db):
    """reserve over cap with allow_defer=False → outcome='block'; ytd unchanged."""
    user_id = make_user(db)
    _set_ytd(db, user_id, CAP - 1)

    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    decision = reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()

    assert decision.outcome == "block"
    assert _get_ytd(db, user_id) == CAP - 1  # unchanged
    assert _get_cap_reserved(db, order_id) == 0


# ---------------------------------------------------------------------------
# reserve — idempotency (same order called twice)
# ---------------------------------------------------------------------------


def test_reserve_idempotent(db):
    """reserve twice on the same order → 2nd call returns 'allow'; ytd increased only once."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    first = reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()
    assert first.outcome == "allow"
    assert _get_ytd(db, user_id) == 2000

    second = reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()

    assert second.outcome == "allow"
    assert _get_ytd(db, user_id) == 2000  # NOT doubled
    assert _get_cap_reserved(db, order_id) == 2000  # not doubled


# ---------------------------------------------------------------------------
# release — after successful reserve
# ---------------------------------------------------------------------------


def test_release_after_reserve(db):
    """release after reserve → ytd back to pre-reserve value; cap_reserved_cents == 0."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()
    assert _get_ytd(db, user_id) == 2000

    release_gift_card_cap(db, order_id)
    db.commit()

    assert _get_ytd(db, user_id) == 0
    assert _get_cap_reserved(db, order_id) == 0


# ---------------------------------------------------------------------------
# release — idempotency (called twice)
# ---------------------------------------------------------------------------


def test_release_idempotent(db):
    """release twice → no double-decrement; ytd stays at 0 after first release."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    reserve_gift_card_cap(db, order_id, allow_defer=False)
    db.commit()

    release_gift_card_cap(db, order_id)
    db.commit()
    assert _get_ytd(db, user_id) == 0

    release_gift_card_cap(db, order_id)  # second call — should be no-op
    db.commit()

    assert _get_ytd(db, user_id) == 0
    assert _get_cap_reserved(db, order_id) == 0


# ---------------------------------------------------------------------------
# release — never-reserved order
# ---------------------------------------------------------------------------


def test_release_never_reserved(db):
    """release on an order with cap_reserved_cents == 0 → no-op (ytd untouched)."""
    user_id = make_user(db)
    _set_ytd(db, user_id, 5000)

    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")
    # cap_reserved_cents starts at 0 (server default), never called reserve

    release_gift_card_cap(db, order_id)
    db.commit()

    assert _get_ytd(db, user_id) == 5000  # untouched


# ---------------------------------------------------------------------------
# next_jan_1_utc
# ---------------------------------------------------------------------------


def test_next_jan_1_utc():
    """next_jan_1_utc() → datetime(now.year + 1, 1, 1, tzinfo=UTC)."""
    result = next_jan_1_utc()
    expected_year = datetime.now(UTC).year + 1
    assert result == datetime(expected_year, 1, 1, tzinfo=UTC)
    assert result.tzinfo == UTC
