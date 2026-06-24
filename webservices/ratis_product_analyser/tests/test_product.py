from __future__ import annotations

import uuid
from datetime import UTC
from decimal import Decimal

import pytest
from ratis_core.models.price import PriceConsensus
from ratis_core.models.product import Product
from ratis_core.models.store import Store

from tests.conftest import make_token


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def product(db) -> Product:
    p = Product(
        ean="3017620422003",
        name="Nutella 400g",
        source="off",
        # OFF products carry quantity info in product_quantity[_unit] only ;
        # the legacy ``unit`` column is reserved for internal SKUs (kg/l/unit)
        # — PG ``off_no_unit`` CHECK enforces it.
        brands="Ferrero",
        product_quantity=400,
        product_quantity_unit="g",
        storage_type="ambient",
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def consensus(db, store, product) -> PriceConsensus:
    from datetime import datetime

    now = datetime.now(UTC)
    c = PriceConsensus(
        id=uuid.uuid4(),
        store_id=store.id,
        product_ean=product.ean,
        price=250,
        trust_score=Decimal("80.00"),
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(c)
    db.flush()
    db.commit()
    return c


@pytest.fixture
def nearby_store(db) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Carrefour Market",
        retailer="carrefour",
        address="2 rue du Marché",
        city="Paris",
        postal_code="75001",
        lat=48.8570,
        lng=2.3530,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


@pytest.fixture
def nearby_consensus(db, nearby_store, product) -> PriceConsensus:
    from datetime import datetime

    now = datetime.now(UTC)
    c = PriceConsensus(
        id=uuid.uuid4(),
        store_id=nearby_store.id,
        product_ean=product.ean,
        price=230,
        trust_score=Decimal("70.00"),
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(c)
    db.flush()
    db.commit()
    return c


# ── tests ─────────────────────────────────────────────────────────────────────


class TestGetProduct:
    def test_returns_200_with_product(self, client, user, product):
        resp = client.get(f"/api/v1/product/{product.ean}", headers=_auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["product"]["ean"] == product.ean
        assert body["product"]["name"] == "Nutella 400g"
        assert body["product"]["brand"] == "Ferrero"
        assert body["product"]["storage_type"] == "ambient"
        assert body["local_price"] is None
        assert body["nearby_prices"] == []

    def test_product_not_found_returns_404(self, client, user):
        resp = client.get("/api/v1/product/1234567890123", headers=_auth(user))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "product_not_found"

    def test_with_store_id_returns_local_price(self, client, user, product, store, consensus):
        resp = client.get(
            f"/api/v1/product/{product.ean}",
            params={"store_id": str(store.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["local_price"] is not None
        assert body["local_price"]["store_id"] == str(store.id)
        # price_cents is an integer number of cents (int-cents).
        assert body["local_price"]["price_cents"] == 250
        assert isinstance(body["local_price"]["price_cents"], int)

    def test_with_store_id_no_consensus_returns_null_local_price(self, client, user, product, store):
        resp = client.get(
            f"/api/v1/product/{product.ean}",
            params={"store_id": str(store.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 200
        assert resp.json()["local_price"] is None

    def test_with_coordinates_returns_nearby_prices(self, client, user, product, nearby_store, nearby_consensus):
        resp = client.get(
            f"/api/v1/product/{product.ean}",
            params={"user_lat": 48.8566, "user_lng": 2.3522},
            headers=_auth(user),
        )
        assert resp.status_code == 200
        nearby = resp.json()["nearby_prices"]
        assert len(nearby) >= 1
        assert nearby[0]["store_id"] == str(nearby_store.id)
        # price_cents is an integer number of cents (int-cents).
        assert nearby[0]["price_cents"] == 230
        assert isinstance(nearby[0]["price_cents"], int)
        assert "distance_km" in nearby[0]

    def test_nearby_excludes_local_store(self, client, user, product, store, consensus, nearby_store, nearby_consensus):
        """When store_id is provided, that store must not appear in nearby_prices."""
        resp = client.get(
            f"/api/v1/product/{product.ean}",
            params={
                "store_id": str(store.id),
                "user_lat": 48.8566,
                "user_lng": 2.3522,
            },
            headers=_auth(user),
        )
        body = resp.json()
        nearby_ids = [p["store_id"] for p in body["nearby_prices"]]
        assert str(store.id) not in nearby_ids

    def test_no_token_returns_401(self, client, product):
        resp = client.get(f"/api/v1/product/{product.ean}")
        assert resp.status_code == 401

    def test_invalid_ean_returns_422(self, client, user):
        resp = client.get("/api/v1/product/not-an-ean", headers=_auth(user))
        assert resp.status_code == 422

    def test_ean8_accepted(self, client, user):
        """EAN-8 format (8 digits) must be accepted — 404 is fine, not 422."""
        resp = client.get("/api/v1/product/12345678", headers=_auth(user))
        assert resp.status_code == 404

    def test_partial_coordinates_returns_empty_nearby(self, client, user, product, nearby_store, nearby_consensus):
        """Only one coordinate provided → nearby_prices stays empty."""
        resp = client.get(
            f"/api/v1/product/{product.ean}",
            params={"user_lat": 48.8566},  # user_lng missing
            headers=_auth(user),
        )
        assert resp.status_code == 200
        assert resp.json()["nearby_prices"] == []

    def test_response_shape(self, client, user, product):
        """Response always has product, local_price, nearby_prices keys."""
        resp = client.get(f"/api/v1/product/{product.ean}", headers=_auth(user))
        assert set(resp.json().keys()) == {"product", "local_price", "nearby_prices"}
