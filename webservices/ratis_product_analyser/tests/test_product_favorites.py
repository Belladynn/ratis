"""Tests for product favorites CRUD."""

from __future__ import annotations

import uuid
from datetime import UTC

import pytest
from ratis_core.models.product import Product, ProductFavorite
from ratis_core.models.user import User

from tests.conftest import make_token


def _auth(user):
    return {"Authorization": f"Bearer {make_token(user.id)}"}


@pytest.fixture
def another_product(db) -> Product:
    p = Product(ean="3017620422099", name="Another 500g", source="off")
    db.add(p)
    db.flush()
    db.commit()
    return p


# ── POST ──────────────────────────────────────────────────────────────────────


def test_add_favorite(client, user, product):
    resp = client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"favorited": True}


def test_add_favorite_persisted(client, user, product, db):
    client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    db.expire_all()
    fav = db.get(ProductFavorite, (user.id, product.ean))
    assert fav is not None


def test_add_favorite_idempotent(client, user, product):
    r1 = client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    r2 = client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_add_unknown_product_returns_404(client, user):
    resp = client.post("/api/v1/product/9999999999999/favorite", headers=_auth(user))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "product_not_found"


def test_add_no_token_returns_401(client, product):
    resp = client.post(f"/api/v1/product/{product.ean}/favorite")
    assert resp.status_code == 401


# ── DELETE ────────────────────────────────────────────────────────────────────


def test_delete_favorite(client, user, product):
    client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    resp = client.delete(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"favorited": False}


def test_delete_favorite_idempotent_when_absent(client, user, product):
    resp = client.delete(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"favorited": False}


def test_delete_unknown_product_returns_404(client, user):
    resp = client.delete("/api/v1/product/9999999999999/favorite", headers=_auth(user))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "product_not_found"


# ── GET list ──────────────────────────────────────────────────────────────────


def test_list_favorites_empty(client, user):
    resp = client.get("/api/v1/product/favorites", headers=_auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_list_favorites_returns_products(client, user, product, another_product):
    client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    client.post(f"/api/v1/product/{another_product.ean}/favorite", headers=_auth(user))
    resp = client.get("/api/v1/product/favorites", headers=_auth(user))
    items = resp.json()["items"]
    assert len(items) == 2
    eans = {i["ean"] for i in items}
    assert eans == {product.ean, another_product.ean}
    # shape
    for i in items:
        assert "name" in i
        assert "created_at" in i


def test_list_favorites_isolated_per_user(client, user, product, another_product, db):
    _fav_uid = uuid.uuid4()
    other = User(
        id=_fav_uid,
        email="other@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(other)
    db.flush()
    db.commit()
    client.post(f"/api/v1/product/{product.ean}/favorite", headers=_auth(user))
    client.post(f"/api/v1/product/{another_product.ean}/favorite", headers=_auth(other))
    resp = client.get("/api/v1/product/favorites", headers=_auth(user))
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ean"] == product.ean


def test_list_favorites_ordered_newest_first(client, user, product, another_product, db):
    """In tests both inserts share a transaction-level now(), so we set
    created_at explicitly to verify the repo's ORDER BY created_at DESC."""
    from datetime import datetime, timedelta

    from ratis_core.models.product import ProductFavorite

    old = ProductFavorite(
        user_id=user.id,
        product_ean=product.ean,
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    new = ProductFavorite(
        user_id=user.id,
        product_ean=another_product.ean,
        created_at=datetime.now(UTC),
    )
    db.add_all([old, new])
    db.flush()
    db.commit()
    resp = client.get("/api/v1/product/favorites", headers=_auth(user))
    items = resp.json()["items"]
    assert items[0]["ean"] == another_product.ean
    assert items[1]["ean"] == product.ean


def test_list_favorites_no_token_returns_401(client):
    resp = client.get("/api/v1/product/favorites")
    assert resp.status_code == 401
