"""TDD — POST /rewards/cashback/process-retroactive

Internal endpoint called by ratis_batch_store_validation when a user_suggested
store flips from validation_status='pending' to 'confirmed'. Iterates all
receipts attached to the store still in store_status='pending', credits cashback
on their accepted scans (with affiliate offers), and flips them to
store_status='confirmed'.

Idempotent — the WHERE store_status='pending' filter naturally excludes
already-processed receipts on a re-run.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import text

from tests.conftest import (
    make_affiliate_offer,
    make_brand,
    make_product,
    make_user,
)


def _make_store(db, *, validation_status: str = "pending") -> uuid.UUID:
    store_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO stores (id, name, source, validation_status, lat, lng, "
            "is_disabled, created_at, updated_at) "
            "VALUES (:id, 'User Store', 'user_suggested', :vs, 0, 0, false, now(), now())"
        ),
        {"id": store_id, "vs": validation_status},
    )
    db.commit()
    return store_id


def _make_receipt(
    db,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    store_status: str = "pending",
) -> uuid.UUID:
    receipt_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO receipts "
            "    (id, user_id, store_id, purchased_at, store_status, "
            "     image_r2_key, created_at, updated_at) "
            "VALUES (:id, :uid, :sid, :pat, :ss, :key, now(), now())"
        ),
        {
            "id": receipt_id,
            "uid": user_id,
            "sid": store_id,
            "pat": date.today(),
            "ss": store_status,
            "key": f"{receipt_id}.jpg",
        },
    )
    db.commit()
    return receipt_id


def _make_scan(
    db,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    receipt_id: uuid.UUID,
    product_ean: str,
    price: int = 250,
) -> uuid.UUID:
    scan_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO scans "
            "    (id, user_id, store_id, receipt_id, product_ean, price, quantity, "
            "     scan_type, status, store_status, scanned_at, status_updated_at) "
            "VALUES (:id, :uid, :sid, :rid, :ean, :price, 1, 'receipt', 'accepted', "
            "        'confirmed', now(), now())"
        ),
        {
            "id": scan_id,
            "uid": user_id,
            "sid": store_id,
            "rid": receipt_id,
            "ean": product_ean,
            "price": price,
        },
    )
    db.commit()
    return scan_id


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_retroactive_requires_internal_key(raw_client):
    resp = raw_client.post(
        "/api/v1/rewards/cashback/process-retroactive",
        json={"store_id": str(uuid.uuid4())},
    )
    # Internal-auth pattern across rewards = 403 (see test_cashback_detection.py
    # test_scan_detected_requires_internal_key).
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Functional
# ---------------------------------------------------------------------------


def test_retroactive_credits_pending_receipts(client, db):
    """A store with N pending receipts → N cashback credits + receipts flipped."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    # Distinct products so the unique (user_id, store_id, product_ean, scanned_at)
    # constraint on scans is never tripped by the synthetic same-timestamp inserts.
    eans: list[str] = []
    for _ in range(3):
        e = make_product(db, brand_id=brand_id)
        make_affiliate_offer(db, product_ean=e, brand_id=brand_id, cashback_rate=0.10)
        eans.append(e)
    store_id = _make_store(db, validation_status="confirmed")

    # 3 pending receipts, each with 1 accepted scan on a different offered product.
    receipts = []
    for ean in eans:
        rid = _make_receipt(db, user_id=user_id, store_id=store_id, store_status="pending")
        _make_scan(
            db,
            user_id=user_id,
            store_id=store_id,
            receipt_id=rid,
            product_ean=ean,
            price=250,
        )
        receipts.append(rid)

    resp = client.post(
        "/api/v1/rewards/cashback/process-retroactive",
        json={"store_id": str(store_id)},
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["processed_receipts"] == 3
    assert body["total_cashback_cents"] == 75  # 3 × round(0.10 × 250) = 75

    # Each receipt now confirmed.
    rows = db.execute(
        text("SELECT store_status FROM receipts WHERE store_id = :sid"),
        {"sid": store_id},
    ).all()
    assert all(r.store_status == "confirmed" for r in rows)

    # 3 cashback transactions created.
    n = db.execute(
        text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).scalar()
    assert n == 3


def test_retroactive_idempotent_second_call_no_op(client, db):
    """A second call against the same store credits 0 — receipts already flipped."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    store_id = _make_store(db, validation_status="confirmed")
    rid = _make_receipt(db, user_id=user_id, store_id=store_id, store_status="pending")
    _make_scan(
        db,
        user_id=user_id,
        store_id=store_id,
        receipt_id=rid,
        product_ean=ean,
        price=250,
    )

    first = client.post(
        "/api/v1/rewards/cashback/process-retroactive",
        json={"store_id": str(store_id)},
    )
    assert first.status_code == 200
    assert first.json()["processed_receipts"] == 1

    second = client.post(
        "/api/v1/rewards/cashback/process-retroactive",
        json={"store_id": str(store_id)},
    )
    assert second.status_code == 200
    assert second.json()["processed_receipts"] == 0
    assert second.json()["total_cashback_cents"] == 0

    # Still exactly 1 cashback transaction.
    n = db.execute(
        text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :uid AND type = 'CREDIT'"),
        {"uid": user_id},
    ).scalar()
    assert n == 1


def test_retroactive_total_excludes_unrelated_credits(client, db):
    """``total_cashback_cents`` reports only the credits inserted by this
    call — a pre-existing CREDIT for another user must not inflate it
    (RW-07: the old global SUM(amount) over cashback_transactions leaked
    concurrent rows into the delta)."""
    # Another user with a pre-existing CREDIT, unrelated to this store.
    other_user = make_user(db)
    other_brand = make_brand(db)
    other_ean = make_product(db, brand_id=other_brand)
    other_offer = make_affiliate_offer(db, product_ean=other_ean, brand_id=other_brand, cashback_rate=0.10)
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, "
            "     affiliate_offer_id, boost_applied) "
            "VALUES (:id, :uid, 'CREDIT', 9999, 'pending', :ean, :oid, false)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": other_user,
            "ean": other_ean,
            "oid": other_offer,
        },
    )
    db.commit()

    user_id = make_user(db)
    brand_id = make_brand(db)
    ean = make_product(db, brand_id=brand_id)
    make_affiliate_offer(db, product_ean=ean, brand_id=brand_id, cashback_rate=0.10)
    store_id = _make_store(db, validation_status="confirmed")
    rid = _make_receipt(db, user_id=user_id, store_id=store_id, store_status="pending")
    _make_scan(
        db,
        user_id=user_id,
        store_id=store_id,
        receipt_id=rid,
        product_ean=ean,
        price=250,
    )

    resp = client.post(
        "/api/v1/rewards/cashback/process-retroactive",
        json={"store_id": str(store_id)},
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["processed_receipts"] == 1
    # 1 × round(0.10 × 250) = 25 — the unrelated 9999 credit is excluded.
    assert body["total_cashback_cents"] == 25


def test_retroactive_skips_receipts_without_offer(client, db):
    """Pending receipts whose scans don't match an affiliate offer still flip
    to confirmed, but produce no cashback transactions."""
    user_id = make_user(db)
    brand_id = make_brand(db)
    ean_no_offer = make_product(db, brand_id=brand_id)
    # No affiliate_offer for ean_no_offer.
    store_id = _make_store(db, validation_status="confirmed")
    rid = _make_receipt(db, user_id=user_id, store_id=store_id, store_status="pending")
    _make_scan(
        db,
        user_id=user_id,
        store_id=store_id,
        receipt_id=rid,
        product_ean=ean_no_offer,
        price=199,
    )

    resp = client.post(
        "/api/v1/rewards/cashback/process-retroactive",
        json={"store_id": str(store_id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed_receipts"] == 1
    assert body["total_cashback_cents"] == 0

    receipt_status = db.execute(
        text("SELECT store_status FROM receipts WHERE id = :rid"),
        {"rid": rid},
    ).scalar()
    assert receipt_status == "confirmed"
