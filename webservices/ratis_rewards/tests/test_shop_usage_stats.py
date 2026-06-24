"""TDD — GET /api/v1/rewards/shop/{brand_id}/usage-stats.

Server-side aggregate of the user's gift-card orders for a single brand.
Replaces the client-side reducer in ``ratis_client/app/shop/[brand_id].tsx``
that walked the (paginated, partial) ``useGiftCards()`` list.

Cf F-13 in the V1.1 usage-stats sprint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_user


def _insert_order(
    db,
    *,
    user_id,
    brand_id,
    denomination_cents,
    days_ago,
    status="issued",
    source_type="shop_purchase",
):
    order_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at) "
            "VALUES (:id, :uid, :bid, :denom, :status, "
            "        :stype, :sref, "
            "        now() - make_interval(days => :days))"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination_cents,
            "status": status,
            "stype": source_type,
            "sref": f"sref_{uuid.uuid4().hex[:12]}",
            "days": days_ago,
        },
    )
    db.commit()
    return order_id


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_requires_auth(raw_client):
    brand_id = uuid.uuid4()
    resp = raw_client.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    assert resp.status_code == 401


def test_invalid_uuid_returns_422(user_client, db):
    """Path coercion : non-UUID brand_id → 422 from FastAPI parser."""
    client_inst, bypass = user_client
    bypass(make_user(db))
    resp = client_inst.get("/api/v1/rewards/shop/not-a-uuid/usage-stats")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def test_zero_orders(user_client, db):
    """User has no orders for this brand → all-zero shape (no 404)."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Empty Brand")

    resp = client_inst.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "brand_id": str(brand_id),
        "orders_count": 0,
        "total_saved_cents": 0,
        "first_order_at": None,
        "last_order_at": None,
    }


def test_aggregates_multiple_orders(user_client, db):
    """count = 3, total = 5+10+20 €, first/last_order_at set."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Brand With Orders")

    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=500, days_ago=30)
    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=1000, days_ago=10)
    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=1)

    resp = client_inst.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    data = resp.json()
    assert data["orders_count"] == 3
    assert data["total_saved_cents"] == 3500
    assert data["first_order_at"] is not None
    assert data["last_order_at"] is not None
    # first_order_at < last_order_at (string ISO comparison is lex-safe).
    assert data["first_order_at"] < data["last_order_at"]


def test_excludes_other_users(user_client, db):
    """Ownership scoping — another user's orders for the same brand are invisible."""
    client_inst, bypass = user_client
    me = make_user(db)
    other = make_user(db)
    brand_id = make_gift_card_brand(db, name="Shared Brand")

    _insert_order(db, user_id=me, brand_id=brand_id, denomination_cents=1000, days_ago=2)
    _insert_order(db, user_id=other, brand_id=brand_id, denomination_cents=5000, days_ago=1)

    bypass(me)
    resp = client_inst.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    data = resp.json()
    assert data["orders_count"] == 1
    assert data["total_saved_cents"] == 1000


def test_excludes_other_brands(user_client, db):
    """Two brands, one order each → each request returns only its brand's stats."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_a = make_gift_card_brand(db, name="Brand A")
    brand_b = make_gift_card_brand(db, name="Brand B")

    _insert_order(db, user_id=user_id, brand_id=brand_a, denomination_cents=1000, days_ago=5)
    _insert_order(db, user_id=user_id, brand_id=brand_b, denomination_cents=5000, days_ago=2)

    resp_a = client_inst.get(f"/api/v1/rewards/shop/{brand_a}/usage-stats").json()
    resp_b = client_inst.get(f"/api/v1/rewards/shop/{brand_b}/usage-stats").json()
    assert resp_a["orders_count"] == 1
    assert resp_a["total_saved_cents"] == 1000
    assert resp_b["orders_count"] == 1
    assert resp_b["total_saved_cents"] == 5000


def test_excludes_failed_orders(user_client, db):
    """Failed orders never produced a saving — must not be counted."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Brand C")

    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=3, status="issued")
    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=2, status="failed")
    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=1000, days_ago=1, status="pending")

    resp = client_inst.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    data = resp.json()
    # issued + pending count (3000), failed (5000) excluded.
    assert data["orders_count"] == 2
    assert data["total_saved_cents"] == 3000


def test_excludes_churned_orders(user_client, db):
    """Churned orders (H3 — migration 20260517_1600_gift_card_churned_status)
    must be excluded just like 'failed' — the user never received the card."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Brand Churned")

    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=4, status="issued")
    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=3000, days_ago=3, status="churned")
    _insert_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=2, status="failed")

    resp = client_inst.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    data = resp.json()
    # Only the 'issued' order (2000) counts; churned + failed are excluded.
    assert data["orders_count"] == 1
    assert data["total_saved_cents"] == 2000


def test_includes_all_source_types(user_client, db):
    """Orders from all source_types (shop_purchase, annual_subscription,
    referral, battlepass...) count toward "total saved" — the user
    received the gift card regardless of how the order was created.
    """
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Brand D")

    _insert_order(
        db, user_id=user_id, brand_id=brand_id, denomination_cents=1000, days_ago=10, source_type="shop_purchase"
    )
    _insert_order(
        db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=5, source_type="annual_subscription"
    )

    resp = client_inst.get(f"/api/v1/rewards/shop/{brand_id}/usage-stats")
    data = resp.json()
    assert data["orders_count"] == 2
    assert data["total_saved_cents"] == 3000
