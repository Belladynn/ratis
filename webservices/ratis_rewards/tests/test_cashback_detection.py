"""
TDD — POST /rewards/cashback/scan-detected
         GET  /rewards/cashback/balance

Amounts are INTEGER centimes. 0.25€ = 25 centimes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import (
    make_affiliate_offer,
    make_brand,
    make_product,
    make_scan,
    make_subscription,
    make_user,
)

# ---------------------------------------------------------------------------
# POST /rewards/cashback/scan-detected
# ---------------------------------------------------------------------------


def test_scan_detected_no_offer(client, db):
    """When no active offer matches the EAN, no transaction is created."""
    user_id = make_user(db)
    scan_id = make_scan(db, user_id=user_id)

    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": "1234567890123", "price": 250, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200

    count = db.execute(
        text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert count == 0


def test_scan_detected_creates_credit_non_subscriber(client, db):
    """Offer matched, non-subscriber: CREDIT pending, no balance credit."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    scan_id = make_scan(db, user_id=user_id)

    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": ean, "price": 250, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200

    tx = db.execute(
        text("SELECT type, amount, status, distributed_at FROM cashback_transactions WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    assert tx is not None
    assert tx.type == "CREDIT"
    assert tx.amount == 25  # round(0.10 * 250) = 25 centimes
    assert tx.status == "pending"
    assert tx.distributed_at is None  # not a subscriber

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 0


def test_scan_detected_creates_credit_subscriber(client, db):
    """Offer matched, subscriber: CREDIT pending with distributed_at set, balance credited."""
    user_id = make_user(db)
    make_subscription(db, user_id)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    scan_id = make_scan(db, user_id=user_id)

    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": ean, "price": 250, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200

    tx = db.execute(
        text("SELECT distributed_at FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).first()
    assert tx is not None
    assert tx.distributed_at is not None  # subscriber advance

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 25  # 25 centimes


def test_scan_detected_idempotent(client, db):
    """Calling scan-detected twice for the same scan+ean creates only one CREDIT."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)
    scan_id = make_scan(db, user_id=user_id)

    payload = {
        "user_id": str(user_id),
        "receipt_lines": [{"ean": ean, "price": 250, "scan_id": str(scan_id)}],
    }
    client.post("/api/v1/rewards/cashback/scan-detected", json=payload)
    client.post("/api/v1/rewards/cashback/scan-detected", json=payload)

    count = db.execute(
        text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).scalar()
    assert count == 1


def test_scan_detected_multiple_lines_partial(client, db):
    """Two receipt lines (each with own scan_id), one EAN has an offer, the other doesn't."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean_with = make_product(db, ean="1111111111111", brand_id=brand_id)
    ean_without = make_product(db, ean="2222222222222", brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean_with, brand_id=brand_id)
    scan_id_1 = make_scan(db, user_id=user_id)
    scan_id_2 = make_scan(db, user_id=user_id)

    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [
                {"ean": ean_with, "price": 250, "scan_id": str(scan_id_1)},
                {"ean": ean_without, "price": 110, "scan_id": str(scan_id_2)},
            ],
        },
    )
    assert resp.status_code == 200

    count = db.execute(
        text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).scalar()
    assert count == 1


def test_scan_detected_requires_internal_key(raw_client):
    resp = raw_client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(uuid.uuid4()),
            "receipt_lines": [],
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /rewards/cashback/balance
# ---------------------------------------------------------------------------


def test_get_balance_empty(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)

    resp = client_inst.get("/api/v1/rewards/cashback/balance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cashback_balance"] == 0
    assert data["pending"] == []


def test_get_balance_with_pending_shows_boost_info(user_client, db):
    """A fresh CREDIT within boost window exposes boost_cost_cab."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)

    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied) "
            "VALUES (:id, :uid, 'CREDIT', 25, 'pending', :ean, :offer_id, false)"
        ),
        {"id": tx_id, "uid": user_id, "ean": ean, "offer_id": offer_id},
    )
    db.commit()

    bypass(user_id)
    resp = client_inst.get("/api/v1/rewards/cashback/balance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cashback_balance"] == 0
    assert len(data["pending"]) == 1
    item = data["pending"][0]
    assert item["amount"] == 25  # 25 centimes
    assert "boost_cost_cab" in item  # window open
    assert "boost_available_until" in item
    assert item["boost_cost_cab"] == 30  # round(25 * 1.2) = 30


def test_get_balance_already_boosted_hides_boost_info(user_client, db):
    """A CREDIT already boosted should not show boost fields."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)

    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied) "
            "VALUES (:id, :uid, 'CREDIT', 25, 'pending', :ean, :offer_id, true)"
        ),
        {"id": tx_id, "uid": user_id, "ean": ean, "offer_id": offer_id},
    )
    db.commit()

    bypass(user_id)
    resp = client_inst.get("/api/v1/rewards/cashback/balance")
    data = resp.json()
    item = data["pending"][0]
    assert "boost_cost_cab" not in item
    assert "boost_available_until" not in item


def test_get_balance_expired_window_hides_boost_info(user_client, db):
    """A CREDIT beyond the boost window should not show boost fields."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    offer_id = make_affiliate_offer(db, product_ean=ean, brand_id=brand_id)

    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied, created_at) "
            "VALUES (:id, :uid, 'CREDIT', 25, 'pending', :ean, :offer_id, false, "
            "        now() - interval '13 hours')"
        ),
        {"id": tx_id, "uid": user_id, "ean": ean, "offer_id": offer_id},
    )
    db.commit()

    bypass(user_id)
    resp = client_inst.get("/api/v1/rewards/cashback/balance")
    data = resp.json()
    item = data["pending"][0]
    assert "boost_cost_cab" not in item
    assert "boost_available_until" not in item


# ===========================================================================
# Achievements V1 — hook in cashback_service.detect_cashback (PR4)
# ===========================================================================


def _spy_check_achievements(monkeypatch):
    """Wrap achievement_service.check_achievements to record kwargs without
    altering its dispatcher behaviour."""
    from services import achievement_service

    calls: list[dict] = []
    original = achievement_service.check_achievements

    def wrapper(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(achievement_service, "check_achievements", wrapper)
    return calls


def test_scan_detected_fires_cashback_credited_event(client, db, monkeypatch):
    """A new CREDIT row inserted by detect_cashback fires `cashback_credited`."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    scan_id = make_scan(db, user_id=user_id)

    calls = _spy_check_achievements(monkeypatch)
    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": ean, "price": 250, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200
    cb_calls = [c for c in calls if c["kwargs"].get("event_type") == "cashback_credited"]
    assert len(cb_calls) == 1
    assert cb_calls[0]["kwargs"].get("user_id") == user_id


def test_scan_detected_no_offer_does_not_fire(client, db, monkeypatch):
    """No CREDIT inserted (no offer matched) → hook does NOT fire."""
    user_id = make_user(db)
    scan_id = make_scan(db, user_id=user_id)

    calls = _spy_check_achievements(monkeypatch)
    resp = client.post(
        "/api/v1/rewards/cashback/scan-detected",
        json={
            "user_id": str(user_id),
            "receipt_lines": [{"ean": "1234567890123", "price": 250, "scan_id": str(scan_id)}],
        },
    )
    assert resp.status_code == 200
    cb_calls = [c for c in calls if c["kwargs"].get("event_type") == "cashback_credited"]
    assert cb_calls == []


def test_scan_detected_idempotent_replay_does_not_refire(client, db, monkeypatch):
    """Replaying the same (scan_id, ean) line is idempotent (has_cashback_for_scan
    skips the insert) so the hook must NOT fire on the second call."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    scan_id = make_scan(db, user_id=user_id)

    body = {
        "user_id": str(user_id),
        "receipt_lines": [{"ean": ean, "price": 250, "scan_id": str(scan_id)}],
    }
    # First call — primes the CREDIT row.
    client.post("/api/v1/rewards/cashback/scan-detected", json=body)

    calls = _spy_check_achievements(monkeypatch)
    resp = client.post("/api/v1/rewards/cashback/scan-detected", json=body)
    assert resp.status_code == 200
    cb_calls = [c for c in calls if c["kwargs"].get("event_type") == "cashback_credited"]
    assert cb_calls == []
