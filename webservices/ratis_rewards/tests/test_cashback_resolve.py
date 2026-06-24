"""
TDD — PATCH /admin/cashback/{id}/validate
         PATCH /admin/cashback/{id}/refuse
         POST  /admin/affiliate-offers
         GET   /admin/affiliate-offers

Amounts are INTEGER centimes. 0.25€ = 25 centimes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import (
    make_affiliate_offer,
    make_brand,
    make_product,
    make_user,
)


def _insert_credit(
    db, *, user_id, ean, offer_id, amount=25, status="pending", boost_applied=False, distributed_at_expr=None
):
    tx_id = uuid.uuid4()
    dist = distributed_at_expr or "NULL"
    db.execute(
        text(
            f"INSERT INTO cashback_transactions "
            f"    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            f"     boost_applied, distributed_at) "
            f"VALUES (:id, :uid, 'CREDIT', :amount, :status, :ean, :offer_id, "
            f"        :boosted, {dist})"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "amount": amount,
            "status": status,
            "ean": ean,
            "offer_id": offer_id,
            "boosted": boost_applied,
        },
    )
    db.flush()
    return tx_id


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def test_admin_validate_non_subscriber_credits_balance(admin_client, db):
    """Non-subscriber CREDIT (distributed_at NULL) → confirm + credit balance."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit(db, user_id=user_id, ean=ean, offer_id=offer_id)

    resp = admin_client.patch(f"/api/v1/admin/cashback/{tx_id}/validate")
    assert resp.status_code == 200

    row = db.execute(
        text("SELECT status, distributed_at FROM cashback_transactions WHERE id = :tid"),
        {"tid": tx_id},
    ).first()
    assert row.status == "confirmed"
    assert row.distributed_at is not None

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 25  # 25 centimes


def test_admin_validate_subscriber_no_double_credit(admin_client, db):
    """Subscriber CREDIT (distributed_at already set) → confirm, no extra credit."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit(
        db,
        user_id=user_id,
        ean=ean,
        offer_id=offer_id,
        distributed_at_expr="now()",
    )
    # Simulate that balance was already credited at scan time
    db.execute(
        text("UPDATE user_cashback_balance SET balance = 25 WHERE user_id = :uid"),
        {"uid": user_id},
    )
    db.flush()

    resp = admin_client.patch(f"/api/v1/admin/cashback/{tx_id}/validate")
    assert resp.status_code == 200

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 25  # unchanged, not doubled


def test_admin_validate_already_resolved(admin_client, db):
    """Attempting to validate a confirmed transaction returns 409."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit(db, user_id=user_id, ean=ean, offer_id=offer_id, status="confirmed")

    resp = admin_client.patch(f"/api/v1/admin/cashback/{tx_id}/validate")
    assert resp.status_code == 409


def test_admin_validate_not_found(admin_client, db):
    resp = admin_client.patch(f"/api/v1/admin/cashback/{uuid.uuid4()}/validate")
    assert resp.status_code == 404


def test_resolve_confirmed_inconsistent_state_logs_warning(db, caplog):
    """A pending CREDIT with distributed_at set → resolve logs a warning.

    Audit RW-money F-5 : this state is theoretically impossible (only a
    subscriber advance sets distributed_at, and that also confirms). If it
    is ever reached, resolve_cashback must surface it loudly instead of
    silently skipping the balance credit. Service-level test — the route
    cannot produce the bad state.
    """
    import logging

    from services.cashback_service import resolve_cashback

    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    # status='pending' BUT distributed_at already set — the forbidden state.
    tx_id = _insert_credit(
        db,
        user_id=user_id,
        ean=ean,
        offer_id=offer_id,
        status="pending",
        distributed_at_expr="now()",
    )

    with caplog.at_level(logging.WARNING, logger="services.cashback_service"):
        resolve_cashback(db, tx_id, "confirmed", {})
    db.rollback()

    assert any("resolve_cashback_inconsistent_state" in rec.message for rec in caplog.records), (
        "expected an inconsistent-state warning to be logged"
    )


# ---------------------------------------------------------------------------
# Refuse
# ---------------------------------------------------------------------------


def test_admin_refuse_not_distributed_no_user_debit(admin_client, db):
    """Refused before distribution: user balance unchanged."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit(db, user_id=user_id, ean=ean, offer_id=offer_id)

    resp = admin_client.patch(f"/api/v1/admin/cashback/{tx_id}/refuse")
    assert resp.status_code == 200

    row = db.execute(
        text("SELECT status FROM cashback_transactions WHERE id = :tid"),
        {"tid": tx_id},
    ).first()
    assert row.status == "refused"

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 0  # never credited → no debit needed


def test_admin_refuse_distributed_absorbs_loss(admin_client, db):
    """Refused after subscriber advance: user keeps the money (Ratis absorbs loss)."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit(
        db,
        user_id=user_id,
        ean=ean,
        offer_id=offer_id,
        distributed_at_expr="now()",
    )
    db.execute(
        text("UPDATE user_cashback_balance SET balance = 25 WHERE user_id = :uid"),
        {"uid": user_id},
    )
    db.flush()

    resp = admin_client.patch(f"/api/v1/admin/cashback/{tx_id}/refuse")
    assert resp.status_code == 200

    # Balance kept — Ratis absorbs
    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 25  # 25 centimes kept


def test_admin_refuse_with_boost_refunds_cab(admin_client, db):
    """Refused CREDIT with a BOOST child → BOOST also refused, CAB refunded."""
    user_id = make_user(db)
    # Balance after boost: started at 1000, paid 30 CAB for boost → 970
    db.execute(
        text("UPDATE user_cab_balance SET balance = 970 WHERE user_id = :uid"),
        {"uid": user_id},
    )
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)

    credit_id = _insert_credit(db, user_id=user_id, ean=ean, offer_id=offer_id, boost_applied=True)

    boost_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied, distributed_at, parent_transaction_id) "
            "VALUES (:id, :uid, 'BOOST', 25, 'pending', :ean, :offer_id, "
            "        false, now(), :parent_id)"
        ),
        {
            "id": boost_id,
            "uid": user_id,
            "ean": ean,
            "offer_id": offer_id,
            "parent_id": credit_id,
        },
    )
    db.flush()

    resp = admin_client.patch(f"/api/v1/admin/cashback/{credit_id}/refuse")
    assert resp.status_code == 200

    # BOOST also refused
    boost_row = db.execute(
        text("SELECT status FROM cashback_transactions WHERE id = :bid"),
        {"bid": boost_id},
    ).first()
    assert boost_row.status == "refused"

    # CAB refunded: round(25 * 1.2) = 30 → back to 1000
    cab_bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert cab_bal == 1000


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_admin_endpoints_require_key(raw_client, db):
    tx_id = uuid.uuid4()
    assert raw_client.patch(f"/api/v1/admin/cashback/{tx_id}/validate").status_code == 403
    assert raw_client.patch(f"/api/v1/admin/cashback/{tx_id}/refuse").status_code == 403
    assert raw_client.post("/api/v1/admin/affiliate-offers", json={}).status_code == 403


# ---------------------------------------------------------------------------
# Affiliate offers CRUD
# ---------------------------------------------------------------------------


def test_admin_create_offer(admin_client, db):
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)

    resp = admin_client.post(
        "/api/v1/admin/affiliate-offers",
        json={
            # PG ``provider_check`` only allows affilae|awin|cj.
            "provider": "affilae",
            "external_id": "ext_test_001",
            "product_ean": ean,
            "brand_id": str(brand_id),
            "cashback_rate": 0.10,
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": None,
        },
    )
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_admin_list_offers(admin_client, db):
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)

    resp = admin_client.get("/api/v1/admin/affiliate-offers")
    assert resp.status_code == 200
    offers = resp.json()
    assert len(offers) >= 1
    assert "cashback_rate" in offers[0]


# ---------------------------------------------------------------------------
# Duplicate affiliate_offer constraint
# ---------------------------------------------------------------------------


def test_duplicate_offer_raises(db):
    """(provider, external_id) UNIQUE constraint raises IntegrityError.

    Uses an inner savepoint so the connection stays clean after the expected
    error — preventing the error from corrupting subsequent tests.
    """
    from sqlalchemy.exc import IntegrityError

    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, provider="affilae", external_id="dup-X")
    db.flush()

    with pytest.raises(IntegrityError):  # noqa: PT012
        sp = db.begin_nested()  # inner savepoint — keeps outer transaction intact
        make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, provider="affilae", external_id="dup-X")
        db.flush()
        sp.commit()
    db.rollback()  # roll back to outer savepoint — connection remains usable
