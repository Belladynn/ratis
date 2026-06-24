import uuid
from datetime import UTC, datetime
from decimal import Decimal

from ratis_core.models.price import PriceConsensus
from ratis_core.models.store import Store


class TestPriceLookup:
    """GET /api/v1/price?product_ean=X&store_id=Y"""

    def test_local_consensus_found(self, user_client, db, store, product):
        """When price_consensus exists for this store+product, return local price."""
        now = datetime.now(UTC)
        pc = PriceConsensus(
            store_id=store.id,
            product_ean=product.ean,
            price=389,  # centimes
            trust_score=Decimal("95.00"),
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(pc)
        db.commit()

        resp = user_client.get(f"/api/v1/price?product_ean={product.ean}&store_id={store.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["price"] == 3.89
        assert data["price_source"] == "consensus_local"
        assert data["trust_score"] == 95.0
        assert "warning" not in data

    def test_national_average_fallback(self, user_client, db, store, product):
        """When no local consensus but enough national data, return average."""
        now = datetime.now(UTC)
        # Create 5 consensus entries at OTHER stores (not the requested one)
        for i in range(5):
            other_store = Store(
                name=f"Store {i}",
                retailer="test",
                lat=Decimal("48.850"),
                lng=Decimal("2.350"),
            )
            db.add(other_store)
            db.flush()
            pc = PriceConsensus(
                store_id=other_store.id,
                product_ean=product.ean,
                price=400 + i * 10,  # 400, 410, 420, 430, 440 centimes
                trust_score=Decimal("80.00"),
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(pc)
        db.commit()

        resp = user_client.get(f"/api/v1/price?product_ean={product.ean}&store_id={store.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["price_source"] == "national_average"
        assert data["trust_score"] is None
        assert data["warning"] == "price_not_reliable"
        # avg of 400,410,420,430,440 = 420 centimes = 4.20 EUR
        assert data["price"] == 4.20

    def test_unknown_price(self, user_client, db, store, product):
        """When no data at all, return unknown."""
        resp = user_client.get(f"/api/v1/price?product_ean={product.ean}&store_id={store.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["price"] is None
        assert data["price_source"] == "unknown"
        assert data["warning"] == "price_unknown_insufficient_data"

    def test_insufficient_national_data(self, user_client, db, store, product):
        """When < min datapoints nationally, return unknown."""
        now = datetime.now(UTC)
        # Only 2 entries (below threshold of 5)
        for i in range(2):
            other_store = Store(
                name=f"Store {i}",
                retailer="test",
                lat=Decimal("48.850"),
                lng=Decimal("2.350"),
            )
            db.add(other_store)
            db.flush()
            pc = PriceConsensus(
                store_id=other_store.id,
                product_ean=product.ean,
                price=400,
                trust_score=Decimal("80.00"),
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(pc)
        db.commit()

        resp = user_client.get(f"/api/v1/price?product_ean={product.ean}&store_id={store.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["price_source"] == "unknown"

    def test_missing_params(self, user_client):
        """Missing query params produce 422."""
        resp = user_client.get("/api/v1/price")
        assert resp.status_code == 422

    def test_auth_required(self, client):
        """No JWT produces 401."""
        resp = client.get("/api/v1/price?product_ean=123&store_id=" + str(uuid.uuid4()))
        assert resp.status_code == 401


class TestPriceValidation:
    """GET /price validates product_ean and store existence (audit LO-02)."""

    def test_non_numeric_ean_rejected(self, user_client, store):
        resp = user_client.get(f"/api/v1/price?product_ean=notanean&store_id={store.id}")
        assert resp.status_code == 422

    def test_overlong_ean_rejected(self, user_client, store):
        resp = user_client.get(f"/api/v1/price?product_ean={'1' * 30}&store_id={store.id}")
        assert resp.status_code == 422

    def test_unknown_store_returns_404(self, user_client, product):
        resp = user_client.get(f"/api/v1/price?product_ean={product.ean}&store_id={uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "store_not_found"


class TestResolvePriceTrustGuard:
    """resolve_price tolerates a NULL trust_score (audit LO-12)."""

    def test_none_trust_score_does_not_crash(self, db, store, product, monkeypatch):
        from decimal import Decimal as _D

        from services import price_service

        monkeypatch.setattr(
            price_service.repo,
            "get_local_price",
            lambda *a, **kw: (_D(389), None),
        )
        result = price_service.resolve_price(db, product.ean, store.id)
        assert result.price == 3.89
        assert result.trust_score is None
