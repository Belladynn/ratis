"""
TDD — GET /rewards/gift-cards  &  GET /rewards/gift-cards/{id}
      POST /rewards/gift-cards/annual (internal)

Amounts are INTEGER centimes. 20.00€ = 2000 centimes.
code is null for pending/failed orders, visible only when issued.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_gift_card_order, make_user

# ---------------------------------------------------------------------------
# GET /api/v1/rewards/gift-cards — list
# ---------------------------------------------------------------------------


def test_list_gift_cards_empty(user_client, db):
    """No orders → empty list."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)

    resp = client_inst.get("/api/v1/rewards/gift-cards")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_gift_cards_returns_own_orders(user_client, db):
    """Returns only the authenticated user's orders, ordered by created_at desc."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    other_id = make_user(db)
    brand_id = make_gift_card_brand(db)

    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="issued", code="AMZN-ABC-123")
    make_gift_card_order(db, user_id=other_id, brand_id=brand_id)  # should not appear

    bypass(user_id)
    resp = client_inst.get("/api/v1/rewards/gift-cards")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(order_id)


def test_list_gift_cards_pending_hides_code(user_client, db):
    """Pending order: code must be null in response even if set in DB."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="pending", code=None)

    bypass(user_id)
    resp = client_inst.get("/api/v1/rewards/gift-cards")
    assert resp.status_code == 200
    assert resp.json()[0]["code"] is None


def test_list_gift_cards_issued_shows_code(user_client, db):
    """Issued order: code must be present."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="issued", code="AMZN-XYZ-789")

    bypass(user_id)
    resp = client_inst.get("/api/v1/rewards/gift-cards")
    assert resp.status_code == 200
    assert resp.json()[0]["code"] == "AMZN-XYZ-789"


def test_list_gift_cards_requires_auth(raw_client, db):
    """No JWT → 403."""
    resp = raw_client.get("/api/v1/rewards/gift-cards")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/rewards/gift-cards/{id} — detail
# ---------------------------------------------------------------------------


def test_get_gift_card_pending(user_client, db):
    """Pending order: correct fields, code null."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db, name="Amazon")
    order_id = make_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination=2000,
        status="pending",
        source_type="annual_subscription",
    )

    bypass(user_id)
    resp = client_inst.get(f"/api/v1/rewards/gift-cards/{order_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(order_id)
    assert data["denomination"] == 2000
    assert data["status"] == "pending"
    assert data["source_type"] == "annual_subscription"
    assert data["code"] is None
    assert "brand" in data
    assert data["brand"]["name"] == "Amazon"


def test_get_gift_card_issued(user_client, db):
    """Issued order: code is visible."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="issued", code="TEST-CODE-999")

    bypass(user_id)
    resp = client_inst.get(f"/api/v1/rewards/gift-cards/{order_id}")
    assert resp.status_code == 200
    assert resp.json()["code"] == "TEST-CODE-999"


def test_get_gift_card_not_found(user_client, db):
    """Unknown ID → 404."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)

    resp = client_inst.get(f"/api/v1/rewards/gift-cards/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_gift_card_requires_auth(raw_client, db):
    """No JWT → 403."""
    resp = raw_client.get(f"/api/v1/rewards/gift-cards/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_get_gift_card_other_user_forbidden(user_client, db):
    """Cannot access another user's order → 403."""
    client_inst, bypass = user_client
    owner_id = make_user(db)
    other_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=owner_id, brand_id=brand_id)

    bypass(other_id)
    resp = client_inst.get(f"/api/v1/rewards/gift-cards/{order_id}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/rewards/gift-cards/annual — internal endpoint
# ---------------------------------------------------------------------------


def test_annual_gift_card_creates_order(client, db):
    """Valid request creates a pending gift_card_orders row."""
    from main import app

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db, name="Amazon")
    session_id = f"cs_test_{uuid.uuid4().hex[:8]}"

    original = app.state.cfg["gift_cards"].copy()
    app.state.cfg["gift_cards"]["annual_subscription_brand_id"] = str(brand_id)
    app.state.cfg["gift_cards"]["annual_subscription_denomination"] = 2000
    try:
        resp = client.post(
            "/api/v1/rewards/gift-cards/annual",
            json={"user_id": str(user_id), "stripe_session_id": session_id},
        )
        assert resp.status_code == 200
    finally:
        app.state.cfg["gift_cards"] = original

    row = db.execute(
        text("SELECT status, source_type, source_ref_id, denomination FROM gift_card_orders WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    assert row is not None
    assert row.status == "pending"
    assert row.source_type == "annual_subscription"
    assert row.source_ref_id == session_id
    assert row.denomination == 2000


def test_annual_gift_card_idempotent(client, db):
    """Same session_id called twice → only one order created."""
    from main import app

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db, name="Amazon")
    session_id = f"cs_idem_{uuid.uuid4().hex[:8]}"

    original = app.state.cfg["gift_cards"].copy()
    app.state.cfg["gift_cards"]["annual_subscription_brand_id"] = str(brand_id)
    app.state.cfg["gift_cards"]["annual_subscription_denomination"] = 2000
    try:
        client.post(
            "/api/v1/rewards/gift-cards/annual",
            json={"user_id": str(user_id), "stripe_session_id": session_id},
        )
        resp = client.post(
            "/api/v1/rewards/gift-cards/annual",
            json={"user_id": str(user_id), "stripe_session_id": session_id},
        )
        assert resp.status_code == 200
    finally:
        app.state.cfg["gift_cards"] = original

    count = db.execute(
        text("SELECT COUNT(*) FROM gift_card_orders WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert count == 1


def test_annual_gift_card_requires_internal_auth(raw_client, db):
    """No internal key → 403."""
    user_id = make_user(db)
    resp = raw_client.post(
        "/api/v1/rewards/gift-cards/annual",
        json={"user_id": str(user_id), "stripe_session_id": "cs_test_x"},
    )
    assert resp.status_code == 403


def test_annual_gift_card_brand_not_configured(client, db):
    """No annual_subscription_brand_id → truthful queued:false, no order.

    Audit RW-money F-6 : the endpoint used to return queued:true even
    when no brand was configured and nothing was created — a lie to the
    caller. It must now report queued:false + reason.
    """
    from main import app

    user_id = make_user(db)
    session_id = f"cs_nobrand_{uuid.uuid4().hex[:8]}"

    original = app.state.cfg["gift_cards"].copy()
    app.state.cfg["gift_cards"]["annual_subscription_brand_id"] = ""
    try:
        resp = client.post(
            "/api/v1/rewards/gift-cards/annual",
            json={"user_id": str(user_id), "stripe_session_id": session_id},
        )
        assert resp.status_code == 200
        assert resp.json() == {"queued": False, "reason": "brand_not_configured"}
    finally:
        app.state.cfg["gift_cards"] = original

    count = db.execute(
        text("SELECT COUNT(*) FROM gift_card_orders WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert count == 0
