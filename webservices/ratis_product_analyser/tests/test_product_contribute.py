"""Route-level tests for ``POST /api/v1/product/{ean}/contribute`` (Phase C-5)."""

from __future__ import annotations

import pytest
from ratis_core.models.product import Product
from ratis_core.models.product_contributions import ProductContribution

from tests.conftest import make_token


def _auth(user):
    return {"Authorization": f"Bearer {make_token(user.id)}"}


@pytest.fixture(autouse=True)
def stub_trigger(monkeypatch):
    """Replace the rewards client emit with an in-memory recorder so the
    route does not perform a real HTTP call during tests and so we can
    assert when the mission credit fires.
    """
    calls: list[dict] = []

    def fake_trigger(user_id, action_type, *args, **kwargs):
        calls.append(
            {
                "user_id": user_id,
                "action_type": action_type,
                "qualifier": kwargs.get("qualifier"),
                "quantity": kwargs.get("quantity", 1),
                "idempotency_key": kwargs.get("idempotency_key"),
                "context": kwargs.get("context"),
            }
        )

    monkeypatch.setattr("services.product_contribute_service.trigger_action", fake_trigger)
    return calls


@pytest.fixture
def empty_brand_product(db) -> Product:
    """Product with ``brands=NULL`` — direct-apply path."""
    p = Product(
        ean="3017620422111",
        name="Empty-Brand Test",
        source="off",
        brands=None,
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def filled_brand_product(db) -> Product:
    """Product with ``brands`` already set — pending_review path."""
    p = Product(
        ean="3017620422112",
        name="Filled-Brand Test",
        source="off",
        brands="Already Filled",
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def empty_categories_product(db) -> Product:
    p = Product(
        ean="3017620422113",
        name="Empty-Categories Test",
        source="off",
        categories_tags=None,
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


# ── Happy path : apply directly ───────────────────────────────────────────────


def test_apply_brands_when_field_empty(client, user, empty_brand_product, db, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "applied"
    assert body["applied"] is True
    assert body["field"] == "brands"
    assert body["idempotent"] is False
    assert "id" in body


def test_apply_updates_product_row(client, user, empty_brand_product, db, stub_trigger):
    client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    db.expire_all()
    p = db.get(Product, empty_brand_product.ean)
    assert p.brands == "Nutella"


def test_apply_inserts_contribution_row(client, user, empty_brand_product, db, stub_trigger):
    client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    db.expire_all()
    rows = db.query(ProductContribution).filter_by(product_ean=empty_brand_product.ean, user_id=user.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.field == "brands"
    assert row.value_text == "Nutella"
    assert row.value_array is None
    assert row.status == "applied"


def test_apply_fires_trigger_action(client, user, empty_brand_product, stub_trigger):
    client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert len(stub_trigger) == 1
    call = stub_trigger[0]
    assert call["action_type"] == "fill_product_field"
    assert call["qualifier"] is None
    assert call["quantity"] == 1
    assert call["idempotency_key"].startswith("contribution:")
    assert call["context"]["product_ean"] == empty_brand_product.ean


def test_apply_array_field_succeeds(client, user, empty_categories_product, db, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_categories_product.ean}/contribute",
        json={
            "field": "categories_tags",
            "value": ["en:dairies", "en:cheeses"],
        },
        headers=_auth(user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "applied"
    db.expire_all()
    p = db.get(Product, empty_categories_product.ean)
    assert p.categories_tags == ["en:dairies", "en:cheeses"]


# ── Pending review path ───────────────────────────────────────────────────────


def test_filled_field_queues_for_review(client, user, filled_brand_product, db, stub_trigger):
    """Filled target field → pending_review, products row UNCHANGED."""
    resp = client.post(
        f"/api/v1/product/{filled_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending_review"
    assert resp.json()["applied"] is False

    db.expire_all()
    p = db.get(Product, filled_brand_product.ean)
    assert p.brands == "Already Filled"


def test_pending_review_does_not_fire_trigger(client, user, filled_brand_product, stub_trigger):
    client.post(
        f"/api/v1/product/{filled_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert stub_trigger == [], "pending_review must NOT credit missions"


# ── Validation ────────────────────────────────────────────────────────────────


def test_array_value_for_scalar_field_returns_422(client, user, empty_brand_product, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": ["nutella"]},
        headers=_auth(user),
    )
    assert resp.status_code == 422
    assert "contribution" in resp.json()["detail"]


def test_scalar_value_for_array_field_returns_422(client, user, empty_categories_product, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_categories_product.ean}/contribute",
        json={"field": "categories_tags", "value": "en:dairies"},
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_value_too_long_returns_422(client, user, empty_brand_product, stub_trigger):
    huge = "x" * 250
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": huge},
        headers=_auth(user),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "contribution_value_too_long"


def test_invalid_tag_shape_returns_422(client, user, empty_categories_product, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_categories_product.ean}/contribute",
        json={"field": "categories_tags", "value": ["dairies"]},  # missing prefix
        headers=_auth(user),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "contribution_value_invalid_tag"


def test_empty_array_returns_422(client, user, empty_categories_product, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_categories_product.ean}/contribute",
        json={"field": "categories_tags", "value": []},
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_empty_string_returns_422(client, user, empty_brand_product, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "   "},
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_control_chars_returns_422(client, user, empty_brand_product, stub_trigger):
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella\x01evil"},
        headers=_auth(user),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "contribution_value_invalid_chars"


def test_too_many_tags_returns_422(client, user, empty_categories_product, stub_trigger):
    too_many = [f"en:tag-{i}" for i in range(31)]
    resp = client.post(
        f"/api/v1/product/{empty_categories_product.ean}/contribute",
        json={"field": "categories_tags", "value": too_many},
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_unknown_field_returns_422(client, user, empty_brand_product, stub_trigger):
    """Pydantic Literal validates first → FastAPI returns 422 with
    a structured ``detail`` array (not our ``contribution_*`` codes)."""
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "nutriscore", "value": "A"},
        headers=_auth(user),
    )
    assert resp.status_code == 422


# ── Idempotency window ───────────────────────────────────────────────────────


def test_idempotent_within_24h_returns_200_no_credit(client, user, empty_brand_product, db, stub_trigger):
    """Second submission for the same (user, ean, field) within 24h →
    200 with the original row, no new mission credit, no second
    INSERT.
    """
    r1 = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert r1.status_code == 201
    first_id = r1.json()["id"]
    assert len(stub_trigger) == 1

    r2 = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella Updated"},
        headers=_auth(user),
    )
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True
    assert r2.json()["id"] == first_id

    # Only one row, only one trigger fire.
    db.expire_all()
    rows = db.query(ProductContribution).filter_by(product_ean=empty_brand_product.ean, user_id=user.id).all()
    assert len(rows) == 1
    assert len(stub_trigger) == 1


def test_idempotency_scoped_to_field(client, user, empty_brand_product, db, stub_trigger):
    """A different field on the same product → not idempotent."""
    # Brand fill
    r1 = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert r1.status_code == 201

    # Different field — labels_tags — must NOT be deduped.
    r2 = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "labels_tags", "value": ["en:organic"]},
        headers=_auth(user),
    )
    assert r2.status_code == 201
    assert r2.json()["id"] != r1.json()["id"]
    assert len(stub_trigger) == 2


# ── Anti-spam daily cap ──────────────────────────────────────────────────────


def test_daily_cap_returns_429(client, user, db, stub_trigger, monkeypatch):
    """A user who reaches the per-day contribution cap gets HTTP 429
    with ``detail='contribution_daily_cap_reached'``."""
    monkeypatch.setattr("services.product_contribute_service._load_daily_cap", lambda: 2)
    # Seed `cap` distinct contributions (distinct EAN dodges the 24h
    # idempotency short-circuit).
    for i in range(2):
        ean = f"301762042811{i}"
        db.add(Product(ean=ean, name=f"Cap {i}", source="off", brands=None))
        db.commit()
        resp = client.post(
            f"/api/v1/product/{ean}/contribute",
            json={"field": "brands", "value": f"Brand{i}"},
            headers=_auth(user),
        )
        assert resp.status_code == 201

    # Cap+1 — rejected with 429.
    db.add(Product(ean="3017620428199", name="Over", source="off", brands=None))
    db.commit()
    resp = client.post(
        "/api/v1/product/3017620428199/contribute",
        json={"field": "brands", "value": "Overflow"},
        headers=_auth(user),
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "contribution_daily_cap_reached"


# ── Auth + 404 ────────────────────────────────────────────────────────────────


def test_no_token_returns_401(client, empty_brand_product):
    resp = client.post(
        f"/api/v1/product/{empty_brand_product.ean}/contribute",
        json={"field": "brands", "value": "Nutella"},
    )
    assert resp.status_code == 401


def test_unknown_ean_returns_404(client, user, stub_trigger):
    resp = client.post(
        "/api/v1/product/9999999999999/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "product_not_found"


def test_unknown_ean_does_not_fire_trigger(client, user, stub_trigger):
    client.post(
        "/api/v1/product/9999999999999/contribute",
        json={"field": "brands", "value": "Nutella"},
        headers=_auth(user),
    )
    assert stub_trigger == []
