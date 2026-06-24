"""TDD — cashback amount computation must use Decimal, not float (KP-03).

Audit RW-money finding 3 : ``round(float(rate) * price)`` violates KP-03
(money in float). The fix computes the centimes amount with Decimal
arithmetic and ROUND_HALF_UP so a ``.5`` centime always rounds up — never
banker's-rounding down, never a binary-float representation error.

The divergence is observable at a half-centime boundary :
  rate = 0.0500 (numeric(5,4)), price = 250 centimes
  exact product = 12.50 centimes
  Python ``round(12.50)`` → 12  (banker's rounding, ties-to-even)
  Decimal ROUND_HALF_UP   → 13  (correct — the user keeps the half cent)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text

from tests.conftest import (
    make_affiliate_offer,
    make_brand,
    make_product,
    make_scan,
    make_user,
)


def test_cashback_credit_amount_uses_decimal_round_half_up(client, db):
    """A 0.5-centime product rounds UP — proves Decimal + ROUND_HALF_UP."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    # rate 0.0500 × price 250 = 12.50 centimes exactly.
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.0500)
    scan_id = make_scan(db, user_id=user_id)

    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": ean, "price": 250, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200

    amount = db.execute(
        text("SELECT amount FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).scalar()
    # ROUND_HALF_UP : 12.50 → 13. Banker's round() would give 12.
    assert amount == 13


def test_cashback_credit_amount_matches_decimal_reference(client, db):
    """The stored amount equals the Decimal-quantized reference value."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    rate = Decimal("0.1234")
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=rate)
    scan_id = make_scan(db, user_id=user_id)
    price = 1799  # centimes

    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": ean, "price": price, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200

    expected = int((rate * Decimal(price)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    amount = db.execute(
        text("SELECT amount FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).scalar()
    assert amount == expected


def test_detect_cashback_no_float_call_in_source():
    """Regression : the cashback amount computation must not use ``float(``.

    Belt + suspenders against a future refactor reintroducing the KP-03
    violation. Runs at unit-test speed (source inspection only).
    """
    import inspect

    from services.cashback_service import detect_cashback

    src = inspect.getsource(detect_cashback)
    assert "float(" not in src, (
        "KP-03 regression : detect_cashback computes a money amount with float() — must use Decimal."
    )
