"""
TDD — POST /rewards/cashback/boost/{transaction_id}

Amounts are INTEGER centimes. 0.25€ = 25 centimes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import (
    make_affiliate_offer,
    make_brand,
    make_product,
    make_user,
)


def _insert_credit_tx(db, *, user_id, ean, offer_id, amount=25, boost_applied=False, hours_old=0):
    """Helper to insert a CREDIT cashback_transaction directly (amount in centimes)."""
    tx_id = uuid.uuid4()
    created_expr = f"now() - interval '{hours_old} hours'" if hours_old > 0 else "now()"
    db.execute(
        text(
            f"INSERT INTO cashback_transactions "
            f"    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            f"     boost_applied, created_at) "
            f"VALUES (:id, :uid, 'CREDIT', :amount, 'pending', :ean, :offer_id, "
            f"        :boosted, {created_expr})"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "amount": amount,
            "ean": ean,
            "offer_id": offer_id,
            "boosted": boost_applied,
        },
    )
    db.flush()
    return tx_id


def test_boost_success(user_client, db):
    """Happy path: BOOST created, CAB debited, cashback balance credited, CREDIT marked."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    db.execute(
        text("UPDATE user_cab_balance SET balance = 1000 WHERE user_id = :uid"),
        {"uid": user_id},
    )
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    tx_id = _insert_credit_tx(db, user_id=user_id, ean=ean, offer_id=offer_id, amount=25)

    bypass(user_id)
    resp = client_inst.post(f"/api/v1/rewards/cashback/boost/{tx_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["boost_cost_cab"] == 30  # round(25 * 1.2) = 30

    # BOOST transaction created
    boost = db.execute(
        text(
            "SELECT type, amount, parent_transaction_id, distributed_at "
            "FROM cashback_transactions WHERE parent_transaction_id = :pid"
        ),
        {"pid": tx_id},
    ).first()
    assert boost is not None
    assert boost.type == "BOOST"
    assert boost.amount == 25  # 25 centimes
    assert boost.distributed_at is not None  # boost is immediately distributed

    # CREDIT marked as boosted
    credit = db.execute(
        text("SELECT boost_applied FROM cashback_transactions WHERE id = :tid"),
        {"tid": tx_id},
    ).first()
    assert credit.boost_applied is True

    # Cashback balance credited with boost delta
    cb_bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert cb_bal == 25  # 25 centimes

    # CAB debited: round(25 * 1.2) = 30
    cab_bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert cab_bal == 970


def test_boost_not_found(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)

    resp = client_inst.post(f"/api/v1/rewards/cashback/boost/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "transaction_not_found"


def test_boost_wrong_user(user_client, db):
    """User cannot boost another user's transaction."""
    client_inst, bypass = user_client
    owner_id = make_user(db)
    attacker_id = make_user(db)
    db.execute(
        text("UPDATE user_cab_balance SET balance = 1000 WHERE user_id = :uid"),
        {"uid": attacker_id},
    )
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit_tx(db, user_id=owner_id, ean=ean, offer_id=offer_id)

    bypass(attacker_id)
    resp = client_inst.post(f"/api/v1/rewards/cashback/boost/{tx_id}")
    assert resp.status_code == 404


def test_boost_already_boosted(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit_tx(db, user_id=user_id, ean=ean, offer_id=offer_id, boost_applied=True)

    bypass(user_id)
    resp = client_inst.post(f"/api/v1/rewards/cashback/boost/{tx_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "already_boosted"


def test_boost_window_expired(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit_tx(
        db,
        user_id=user_id,
        ean=ean,
        offer_id=offer_id,
        hours_old=13,  # > 12h window
    )

    bypass(user_id)
    resp = client_inst.post(f"/api/v1/rewards/cashback/boost/{tx_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "boost_window_expired"


def test_boost_insufficient_cab(user_client, db):
    """User has 0 CAB (default from make_user) — cannot pay boost cost."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    tx_id = _insert_credit_tx(db, user_id=user_id, ean=ean, offer_id=offer_id)

    bypass(user_id)
    resp = client_inst.post(f"/api/v1/rewards/cashback/boost/{tx_id}")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "insufficient_cab_balance"
