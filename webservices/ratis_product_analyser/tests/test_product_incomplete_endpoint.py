"""Integration tests for GET /api/v1/product/incomplete."""

from __future__ import annotations

from ratis_core.models.product import Product

from tests.conftest import make_token


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _make_product(db, ean, **kwargs):
    p = Product(
        ean=ean,
        name=kwargs.get("name", "Produit"),
        brands_text=kwargs.get("brands_text", "Brand"),
        categories_tags=kwargs.get("categories_tags", ["en:foods"]),
        labels_tags=kwargs.get("labels_tags", ["en:organic"]),
        source=kwargs.get("source", "off"),
    )
    db.add(p)
    db.commit()
    return p


def test_incomplete_endpoint_requires_auth(client):
    resp = client.get("/api/v1/product/incomplete")
    assert resp.status_code == 401


def test_incomplete_endpoint_returns_items_shape(client, user, db):
    _make_product(db, "9990000000001", brands_text=None)
    resp = client.get("/api/v1/product/incomplete?limit=10", headers=_auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1
    task = body["items"][0]
    assert task["product_ean"] == "9990000000001"
    assert task["product_name"] == "Produit"
    assert task["missing_field"] == "brands"
    assert task["cab_reward"] == 5


def test_incomplete_endpoint_empty_when_all_complete(client, user, db):
    _make_product(db, "9990000000001")  # all fields set
    resp = client.get("/api/v1/product/incomplete", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_incomplete_endpoint_default_limit_10(client, user, db):
    for i in range(15):
        _make_product(db, f"99900000000{i + 10}", brands_text=None)
    resp = client.get("/api/v1/product/incomplete", headers=_auth(user))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 10


def test_incomplete_endpoint_clamps_limit_lower(client, user):
    resp = client.get("/api/v1/product/incomplete?limit=0", headers=_auth(user))
    assert resp.status_code == 422


def test_incomplete_endpoint_clamps_limit_upper(client, user):
    resp = client.get("/api/v1/product/incomplete?limit=51", headers=_auth(user))
    assert resp.status_code == 422
