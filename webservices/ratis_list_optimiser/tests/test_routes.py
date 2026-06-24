"""Integration tests for optimization + route endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from ratis_core.models.price import PriceConsensus
from ratis_core.models.product import Product
from ratis_core.models.shopping import (
    OptimizedRoute,
    ShoppingList,
    ShoppingListItem,
)
from ratis_core.models.store import Store
from services.osrm_client import RouteResult, TripResult

from tests.conftest import make_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(db, *, lat="48.857", lng="2.352", name="TestStore", retailer="test"):
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer=retailer,
        address="1 rue du Test",
        city="Paris",
        postal_code="75001",
        lat=Decimal(lat),
        lng=Decimal(lng),
    )
    db.add(s)
    db.flush()
    return s


def _make_product(db, ean, name="Product"):
    p = Product(ean=ean, name=name, source="off")
    db.add(p)
    db.flush()
    return p


def _make_consensus(db, store_id, ean, price_cents, trust="90.00"):
    now = datetime.now(UTC)
    pc = PriceConsensus(
        store_id=store_id,
        product_ean=ean,
        price=price_cents,
        trust_score=Decimal(trust),
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(pc)
    db.flush()
    return pc


def _make_list_with_items(db, user_id, products):
    """Create a shopping list with items for the given products (list of product objects)."""
    sl = ShoppingList(user_id=user_id)
    db.add(sl)
    db.flush()
    for p in products:
        item = ShoppingListItem(list_id=sl.id, product_ean=p.ean, quantity=1)
        db.add(item)
    db.flush()
    return sl


def _make_route(db, user_id, list_id, *, expired=False, steps=None, status="ready"):
    """Create a stored OptimizedRoute for testing GET/move/remove."""
    if steps is None:
        steps = {
            "stores": [
                {
                    "store_id": str(uuid.uuid4()),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(uuid.uuid4()),
                            "product_ean": "1111111111111",
                            "product_name": "Prod1",
                            "quantity": 1,
                            "price": 2.50,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 2.50,
                }
            ],
            "route_polyline": "encoded_poly",
            "total_stores": 1,
            "total_items": 1,
            "warnings": [],
        }
    expires = datetime.now(UTC) + timedelta(hours=-1 if expired else 48)
    # PG ``expires_after_computed`` : expires_at > computed_at. When the test
    # asks for an expired route, push computed_at further back so the
    # invariant holds while the row remains TTL-expired.
    computed = expires - timedelta(hours=1)
    route = OptimizedRoute(
        user_id=user_id,
        list_id=list_id,
        status=status,
        total_price=Decimal("2.50"),
        total_savings=Decimal("0.00"),
        distance_km=Decimal("1.50"),
        steps=steps,
        computed_at=computed,
        expires_at=expires,
    )
    db.add(route)
    db.flush()
    return route


class _MockOsrmClient:
    """Fake OSRM client that returns deterministic results."""

    def __init__(self, **kwargs):
        pass

    @staticmethod
    def map_transport_mode(mode):
        return "car"

    def trip(self, coordinates, profile="car"):
        n = len(coordinates)
        return TripResult(
            geometry="mock_polyline",
            distance_m=5000.0,
            duration_s=600.0,
            waypoint_order=list(range(n)),
        )

    def route(self, origin, destination, profile="car"):
        return RouteResult(
            geometry="mock_polyline",
            distance_m=2000.0,
            duration_s=300.0,
        )


# ===========================================================================
# POST /api/v1/lists/{list_id}/optimize
# ===========================================================================


class TestOptimize:
    """POST /api/v1/lists/{list_id}/optimize"""

    def test_optimize_enqueues_task(self, user_client, db, user, monkeypatch):
        """Successful optimization pre-check creates a pending route and returns 202."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "1111111111111", "Product1")
        p2 = _make_product(db, "2222222222222", "Product2")
        p3 = _make_product(db, "3333333333333", "Product3")

        store = _make_store(db, lat="48.857", lng="2.352")
        _make_consensus(db, store.id, p1.ean, 250)
        _make_consensus(db, store.id, p2.ean, 350)
        _make_consensus(db, store.id, p3.ean, 150)

        sl = _make_list_with_items(db, user.id, [p1, p2, p3])
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )

        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["status"] == "computing"
        assert "id" in data
        # Task must have been dispatched
        mock_task.delay.assert_called_once()
        call_args = mock_task.delay.call_args[0]
        assert call_args[1] == 48.856  # lat
        assert call_args[2] == 2.351  # lng

    def test_optimize_empty_list(self, user_client, db, user, monkeypatch):
        """Optimizing an empty list returns 422 synchronously."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "empty_list"
        mock_task.delay.assert_not_called()

    def test_optimize_no_position(self, user_client, db, user, monkeypatch):
        """No lat/lng and no ref_lat on user returns 422 synchronously."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "4444444444444", "ProdNP")
        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "no_position"
        mock_task.delay.assert_not_called()

    def test_optimize_not_owner(self, client, db, user, monkeypatch):
        """Optimizing someone else's list returns 403."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)

        from ratis_core.models.user import User

        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            display_name="Other",
            account_type="oauth",
        )
        db.add(other)
        db.flush()

        sl = ShoppingList(user_id=other.id)
        db.add(sl)
        db.commit()

        token = make_token(user.id)
        resp = client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_optimize_no_stores_nearby_still_202(self, user_client, db, user, monkeypatch):
        """No stores nearby — handler still returns 202 (worker handles the failure)."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "5555555555555", "ProdFar")
        # Store far away (London)
        _make_store(db, lat="51.507", lng="-0.127", name="FarStore")

        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "computing"
        mock_task.delay.assert_called_once()

    def test_optimize_list_not_found(self, user_client):
        """Optimizing a non-existent list returns 404."""
        resp = user_client.post(
            f"/api/v1/lists/{uuid.uuid4()}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "list_not_found"

    def test_optimize_enqueue_failure_marks_route_failed(self, user_client, db, user, monkeypatch):
        """If the Celery enqueue (.delay) fails, the route must end up 'failed'.

        A Redis outage must not leave the route stuck in 'computing' forever.
        """
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        mock_task.delay.side_effect = ConnectionError("Redis broker unreachable")
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "6111111111111", "EnqueueFailProd")
        store = _make_store(db, lat="48.857", lng="2.352")
        _make_consensus(db, store.id, p1.ean, 250)
        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )

        # Endpoint still returns 202 — the route exists, just failed to enqueue.
        assert resp.status_code == 202, resp.text
        route_id = uuid.UUID(resp.json()["id"])

        # The route must be persisted as 'failed', not stuck in 'computing'.
        route = db.get(OptimizedRoute, route_id)
        db.refresh(route)
        assert route.status == "failed"

    def test_optimize_rejects_oversized_list(self, user_client, db, user, monkeypatch):
        """LO-20a — a list above max_items_per_list (100) is rejected with 422."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        # 101 items — over the cap
        for i in range(101):
            ean = f"{i:013d}"
            db.add(Product(ean=ean, name=f"P{i}", source="off"))
            db.flush()
            db.add(ShoppingListItem(list_id=sl.id, product_ean=ean, quantity=1))
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "list_too_large"
        mock_task.delay.assert_not_called()
        # No route row created
        assert db.query(OptimizedRoute).filter_by(list_id=sl.id).count() == 0

    def test_optimize_idempotent_while_computing(self, user_client, db, user, monkeypatch):
        """LO-20b — a second optimize while one is still computing returns the
        existing route instead of spawning a duplicate."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "7111111111111", "IdemProd")
        store = _make_store(db, lat="48.857", lng="2.352")
        _make_consensus(db, store.id, p1.ean, 250)
        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        first = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert first.status_code == 202, first.text
        first_id = first.json()["id"]

        second = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert second.status_code == 202, second.text
        # Same route returned — no duplicate
        assert second.json()["id"] == first_id
        # Only one task dispatched, only one route row
        assert mock_task.delay.call_count == 1
        assert db.query(OptimizedRoute).filter_by(list_id=sl.id, status="computing").count() == 1

    def test_optimize_ghost_row_resets_and_creates_new(self, user_client, db, user, monkeypatch):
        """Sentry RATIS-WEBSERVICES-18 — a stale 'computing' row past the
        ``stuck_computing_threshold_minutes`` window (worker crashed mid-task,
        never marked the route terminal) must be auto-reset to ``failed`` and
        replaced with a fresh row, so the user is never stuck waiting for a
        ghost optimization that will never complete.

        Without this guard, the user retry hits the partial unique index
        ``uq_optimized_routes_one_computing_per_list`` → IntegrityError → the
        recovery branch returns the ghost row → infinite stuck state requiring
        manual DB intervention.
        """
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "7222222222222", "GhostProd")
        store = _make_store(db, lat="48.857", lng="2.352")
        _make_consensus(db, store.id, p1.ean, 250)
        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        # Insert a ghost row : status='computing' but computed_at is 15 min ago,
        # well past the 10-min threshold. expires_at kept in the future so the
        # idempotency repo query still sees it (this is exactly the prod case —
        # the row is still "fresh" by TTL but the worker is long-dead).
        now = datetime.now(UTC)
        ghost = OptimizedRoute(
            user_id=user.id,
            list_id=sl.id,
            status="computing",
            total_price=Decimal("0.01"),
            total_savings=Decimal("0"),
            steps={},
            computed_at=now - timedelta(minutes=15),
            expires_at=now + timedelta(hours=47),
        )
        db.add(ghost)
        db.commit()
        ghost_id = ghost.id

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert resp.status_code == 202, resp.text
        new_id = uuid.UUID(resp.json()["id"])

        # A *new* route was created — the ghost was reset, not returned as-is.
        assert new_id != ghost_id
        # The ghost is now marked failed.
        db.refresh(ghost)
        assert ghost.status == "failed"
        # The new task was dispatched (the user gets a real attempt this time).
        mock_task.delay.assert_called_once()
        # Exactly one 'computing' row remains (the new one).
        assert db.query(OptimizedRoute).filter_by(list_id=sl.id, status="computing").count() == 1

    def test_optimize_fresh_computing_row_still_idempotent(self, user_client, db, user, monkeypatch):
        """Counter-test of the ghost-row reset : a *fresh* computing row (within
        the threshold) is still returned as-is by the idempotency guard. The
        ghost-row reset must not regress the LO-20b idempotency contract."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "7333333333333", "FreshIdemProd")
        store = _make_store(db, lat="48.857", lng="2.352")
        _make_consensus(db, store.id, p1.ean, 250)
        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        # Computing row that's 2 minutes old → well within the 10-min threshold
        now = datetime.now(UTC)
        fresh = OptimizedRoute(
            user_id=user.id,
            list_id=sl.id,
            status="computing",
            total_price=Decimal("0.01"),
            total_savings=Decimal("0"),
            steps={},
            computed_at=now - timedelta(minutes=2),
            expires_at=now + timedelta(hours=47),
        )
        db.add(fresh)
        db.commit()
        fresh_id = fresh.id

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/optimize",
            json={"lat": 48.856, "lng": 2.351},
        )
        assert resp.status_code == 202, resp.text
        # Same row returned — no reset, no new row, no new task.
        assert uuid.UUID(resp.json()["id"]) == fresh_id
        db.refresh(fresh)
        assert fresh.status == "computing"
        mock_task.delay.assert_not_called()


# ===========================================================================
# GET /api/v1/routes/{route_id}
# ===========================================================================


class TestGetRoute:
    """GET /api/v1/routes/{route_id}"""

    def test_get_route(self, user_client, db, user):
        """Valid ready route returns 200 with full data."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id)
        db.commit()

        resp = user_client.get(f"/api/v1/routes/{route.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(route.id)
        assert data["status"] == "ready"
        assert "stores" in data

    def test_get_route_computing(self, user_client, db, user):
        """Route with status='computing' returns slim response without stores."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, status="computing", steps={})
        db.commit()

        resp = user_client.get(f"/api/v1/routes/{route.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(route.id)
        assert data["list_id"] == str(sl.id)
        assert data["status"] == "computing"
        assert "stores" not in data

    def test_get_route_updating(self, user_client, db, user):
        """Route with status='updating' returns slim response."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, status="updating")
        db.commit()

        resp = user_client.get(f"/api/v1/routes/{route.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updating"
        assert "stores" not in data

    def test_get_route_failed(self, user_client, db, user):
        """Route with status='failed' returns slim response with status."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, status="failed", steps={})
        db.commit()

        resp = user_client.get(f"/api/v1/routes/{route.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "stores" not in data

    def test_get_route_not_found(self, user_client):
        """Non-existent route returns 404."""
        resp = user_client.get(f"/api/v1/routes/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "route_not_found"

    def test_get_route_expired(self, user_client, db, user):
        """Expired route returns 410."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, expired=True)
        db.commit()

        resp = user_client.get(f"/api/v1/routes/{route.id}")
        assert resp.status_code == 410
        assert resp.json()["detail"] == "route_expired"

    def test_get_route_not_owner(self, client, db, user):
        """Cannot read another user's route."""
        from ratis_core.models.user import User

        _route_uid = uuid.uuid4()
        other = User(
            id=_route_uid,
            email="routeother@ratis.fr",
            display_name="RouteOther",
            account_type="oauth",
        )
        db.add(other)
        db.flush()

        sl = ShoppingList(user_id=other.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, other.id, sl.id)
        db.commit()

        token = make_token(user.id)
        resp = client.get(
            f"/api/v1/routes/{route.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ===========================================================================
# GET /api/v1/lists/{list_id}/route
# ===========================================================================


class TestGetLatestRoute:
    """GET /api/v1/lists/{list_id}/route"""

    def test_get_latest_route(self, user_client, db, user):
        """Returns the latest non-expired route for the list."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id)
        db.commit()

        resp = user_client.get(f"/api/v1/lists/{sl.id}/route")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(route.id)

    def test_get_latest_route_computing(self, user_client, db, user):
        """Returns computing route with slim response."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, status="computing", steps={})
        db.commit()

        resp = user_client.get(f"/api/v1/lists/{sl.id}/route")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(route.id)
        assert data["status"] == "computing"

    def test_no_active_route(self, user_client, db, user):
        """No routes for list returns 404."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.commit()

        resp = user_client.get(f"/api/v1/lists/{sl.id}/route")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "no_active_route"

    def test_list_not_found_for_latest(self, user_client):
        """Non-existent list returns 404."""
        resp = user_client.get(f"/api/v1/lists/{uuid.uuid4()}/route")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "list_not_found"


# ===========================================================================
# POST /api/v1/routes/{route_id}/move-item
# ===========================================================================


class TestMoveItem:
    """POST /api/v1/routes/{route_id}/move-item"""

    def test_move_item(self, user_client, db, user):
        """Move an item between stores updates steps and totals immediately (200)."""
        store_a_id = uuid.uuid4()
        store_b_id = uuid.uuid4()
        item_id = uuid.uuid4()

        s_a = Store(
            id=store_a_id,
            name="StoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        s_b = Store(
            id=store_b_id,
            name="StoreB",
            retailer="brandB",
            address="addr2",
            city="Paris",
            postal_code="75002",
            lat=Decimal("48.860"),
            lng=Decimal("2.355"),
        )
        db.add_all([s_a, s_b])
        db.flush()

        p1 = _make_product(db, "9111111111111", "MoveProduct")
        _make_consensus(db, store_b_id, p1.ean, 300)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        steps = {
            "stores": [
                {
                    "store_id": str(store_a_id),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(item_id),
                            "product_ean": p1.ean,
                            "product_name": "MoveProduct",
                            "quantity": 1,
                            "price": 2.50,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 2.50,
                },
                {
                    "store_id": str(store_b_id),
                    "store_name": "StoreB",
                    "retailer": "brandB",
                    "address": "addr2",
                    "lat": 48.860,
                    "lng": 2.355,
                    "order": 2,
                    "items": [
                        {
                            "item_id": str(uuid.uuid4()),
                            "product_ean": "9222222222222",
                            "product_name": "OtherProduct",
                            "quantity": 1,
                            "price": 4.00,
                            "price_source": "consensus_local",
                            "trust_score": 85.0,
                        }
                    ],
                    "subtotal": 4.00,
                },
            ],
            "route_polyline": "poly",
            "total_stores": 2,
            "total_items": 2,
            "warnings": [],
        }

        route = _make_route(db, user.id, sl.id, steps=steps)
        route.total_price = Decimal("6.50")
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={"item_id": str(item_id), "target_store_id": str(store_b_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ready"
        # Item should now be at store B
        store_b_data = next(s for s in data["stores"] if s["store_id"] == str(store_b_id))
        item_eans = [i["product_ean"] for i in store_b_data["items"]]
        assert p1.ean in item_eans

    def test_move_item_to_store_not_in_route(self, user_client, db, user):
        """Move an item to a DB store absent from the route — new entry created (LO-25)."""
        store_a_id = uuid.uuid4()
        item_id = uuid.uuid4()

        s_a = Store(
            id=store_a_id,
            name="StoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        # Target store exists in DB but is NOT part of the route's steps.
        s_c = _make_store(db, lat="48.870", lng="2.360", name="StoreC", retailer="brandC")
        db.add(s_a)
        db.flush()

        p1 = _make_product(db, "9555555555555", "MoveNewProduct")
        _make_consensus(db, s_c.id, p1.ean, 350)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        steps = {
            "stores": [
                {
                    "store_id": str(store_a_id),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(item_id),
                            "product_ean": p1.ean,
                            "product_name": "MoveNewProduct",
                            "quantity": 1,
                            "price": 2.50,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 2.50,
                }
            ],
            "route_polyline": "poly",
            "total_stores": 1,
            "total_items": 1,
            "warnings": [],
        }
        route = _make_route(db, user.id, sl.id, steps=steps)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={"item_id": str(item_id), "target_store_id": str(s_c.id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        store_ids = [s["store_id"] for s in data["stores"]]
        # Source store A became empty and was dropped; store C is now present.
        assert str(s_c.id) in store_ids
        assert str(store_a_id) not in store_ids

    def test_move_item_to_nonexistent_store(self, user_client, db, user):
        """Move an item to a store_id that does not exist in DB returns 404 (LO-25)."""
        store_a_id = uuid.uuid4()
        item_id = uuid.uuid4()
        s_a = Store(
            id=store_a_id,
            name="StoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        db.add(s_a)
        db.flush()
        p1 = _make_product(db, "9666666666666", "MoveProduct")

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        steps = {
            "stores": [
                {
                    "store_id": str(store_a_id),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(item_id),
                            "product_ean": p1.ean,
                            "product_name": "MoveProduct",
                            "quantity": 1,
                            "price": 2.50,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 2.50,
                }
            ],
            "route_polyline": "poly",
            "total_stores": 1,
            "total_items": 1,
            "warnings": [],
        }
        route = _make_route(db, user.id, sl.id, steps=steps)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={"item_id": str(item_id), "target_store_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "store_not_found"

    def test_move_item_item_not_found(self, user_client, db, user):
        """Item not in route steps returns 404."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={
                "item_id": str(uuid.uuid4()),
                "target_store_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "item_not_found_in_route"

    def test_move_item_expired(self, user_client, db, user):
        """Moving item in expired route returns 410."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, expired=True)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={
                "item_id": str(uuid.uuid4()),
                "target_store_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 410

    def test_move_item_route_not_found(self, user_client):
        """Non-existent route returns 404."""
        resp = user_client.post(
            f"/api/v1/routes/{uuid.uuid4()}/move-item",
            json={
                "item_id": str(uuid.uuid4()),
                "target_store_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/routes/{route_id}/remove-store
# ===========================================================================


class TestRemoveStore:
    """POST /api/v1/routes/{route_id}/remove-store"""

    def test_remove_store(self, user_client, db, user):
        """Removing a store redistributes items to remaining stores (200)."""
        store_a_id = uuid.uuid4()
        store_b_id = uuid.uuid4()

        s_a = Store(
            id=store_a_id,
            name="RemStoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        s_b = Store(
            id=store_b_id,
            name="RemStoreB",
            retailer="brandB",
            address="addr2",
            city="Paris",
            postal_code="75002",
            lat=Decimal("48.860"),
            lng=Decimal("2.355"),
        )
        db.add_all([s_a, s_b])
        db.flush()

        p1 = _make_product(db, "9333333333333", "RemProduct")
        _make_consensus(db, store_b_id, p1.ean, 280)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        steps = {
            "stores": [
                {
                    "store_id": str(store_a_id),
                    "store_name": "RemStoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(uuid.uuid4()),
                            "product_ean": p1.ean,
                            "product_name": "RemProduct",
                            "quantity": 1,
                            "price": 3.00,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 3.00,
                },
                {
                    "store_id": str(store_b_id),
                    "store_name": "RemStoreB",
                    "retailer": "brandB",
                    "address": "addr2",
                    "lat": 48.860,
                    "lng": 2.355,
                    "order": 2,
                    "items": [
                        {
                            "item_id": str(uuid.uuid4()),
                            "product_ean": "9444444444444",
                            "product_name": "KeepProduct",
                            "quantity": 1,
                            "price": 4.50,
                            "price_source": "consensus_local",
                            "trust_score": 85.0,
                        }
                    ],
                    "subtotal": 4.50,
                },
            ],
            "route_polyline": "poly",
            "total_stores": 2,
            "total_items": 2,
            "warnings": [],
        }

        route = _make_route(db, user.id, sl.id, steps=steps)
        route.total_price = Decimal("7.50")
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": str(store_a_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ready"

        # Store A should be gone
        store_ids = [s["store_id"] for s in data["stores"]]
        assert str(store_a_id) not in store_ids
        # Product should have been moved to store B
        store_b_data = next(s for s in data["stores"] if s["store_id"] == str(store_b_id))
        eans = [i["product_ean"] for i in store_b_data["items"]]
        assert p1.ean in eans

    def test_cannot_remove_last_store(self, user_client, db, user):
        """Cannot remove the only remaining store -> 422."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        steps = {
            "stores": [
                {
                    "store_id": str(uuid.uuid4()),
                    "store_name": "OnlyStore",
                    "retailer": "retailer",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [],
                    "subtotal": 0,
                }
            ],
            "route_polyline": None,
            "total_stores": 1,
            "total_items": 0,
            "warnings": [],
        }
        route = _make_route(db, user.id, sl.id, steps=steps)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": steps["stores"][0]["store_id"]},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "cannot_remove_last_store"

    def test_remove_store_expired(self, user_client, db, user):
        """Removing store from expired route returns 410."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, expired=True)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 410

    def test_remove_store_not_found(self, user_client):
        """Non-existent route returns 404."""
        resp = user_client.post(
            f"/api/v1/routes/{uuid.uuid4()}/remove-store",
            json={"store_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404

    def test_remove_store_absent_from_route(self, user_client, db, user):
        """Removing a store_id absent from a multi-store route returns 404 (LO-06)."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        steps = {
            "stores": [
                {
                    "store_id": str(uuid.uuid4()),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [],
                    "subtotal": 0,
                },
                {
                    "store_id": str(uuid.uuid4()),
                    "store_name": "StoreB",
                    "retailer": "brandB",
                    "address": "addr2",
                    "lat": 48.860,
                    "lng": 2.355,
                    "order": 2,
                    "items": [],
                    "subtotal": 0,
                },
            ],
            "route_polyline": None,
            "total_stores": 2,
            "total_items": 0,
            "warnings": [],
        }
        route = _make_route(db, user.id, sl.id, steps=steps)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "store_not_found"


# ===========================================================================
# Audit fixes — store validation, locking, status guard, warnings
# ===========================================================================


def _two_store_steps(store_a_id, store_b_id, item_id, ean):
    """Build a 2-store ``steps`` dict with one movable item at store A."""
    return {
        "stores": [
            {
                "store_id": str(store_a_id),
                "store_name": "StoreA",
                "retailer": "brandA",
                "address": "addr",
                "lat": 48.857,
                "lng": 2.352,
                "order": 1,
                "items": [
                    {
                        "item_id": str(item_id),
                        "product_ean": ean,
                        "product_name": "AuditProduct",
                        "quantity": 1,
                        "price": 2.50,
                        "price_source": "consensus_local",
                        "trust_score": 90.0,
                    }
                ],
                "subtotal": 2.50,
            },
            {
                "store_id": str(store_b_id),
                "store_name": "StoreB",
                "retailer": "brandB",
                "address": "addr2",
                "lat": 48.860,
                "lng": 2.355,
                "order": 2,
                "items": [
                    {
                        "item_id": str(uuid.uuid4()),
                        "product_ean": "9888888888888",
                        "product_name": "KeepProduct",
                        "quantity": 1,
                        "price": 4.00,
                        "price_source": "consensus_local",
                        "trust_score": 85.0,
                    }
                ],
                "subtotal": 4.00,
            },
        ],
        "route_polyline": "poly",
        "total_stores": 2,
        "total_items": 2,
        "warnings": [],
    }


class TestMoveItemStoreValidation:
    """move-item rejects disabled / not-geo-validated target stores (audit LO-01)."""

    def test_move_item_to_disabled_store_rejected(self, user_client, db, user):
        """A disabled target store yields 422 store_unavailable."""
        store_a_id = uuid.uuid4()
        item_id = uuid.uuid4()
        s_a = Store(
            id=store_a_id,
            name="StoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        disabled = Store(
            id=uuid.uuid4(),
            name="DisabledStore",
            retailer="brandX",
            address="x",
            city="Paris",
            postal_code="75003",
            lat=Decimal("48.870"),
            lng=Decimal("2.360"),
            is_disabled=True,
            disabled_at=datetime.now(UTC),
        )
        db.add_all([s_a, disabled])
        db.flush()
        p1 = _make_product(db, "9777777777777", "AuditProduct")

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        steps = {
            "stores": [
                {
                    "store_id": str(store_a_id),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(item_id),
                            "product_ean": p1.ean,
                            "product_name": "AuditProduct",
                            "quantity": 1,
                            "price": 2.50,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 2.50,
                }
            ],
            "route_polyline": "poly",
            "total_stores": 1,
            "total_items": 1,
            "warnings": [],
        }
        route = _make_route(db, user.id, sl.id, steps=steps)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={"item_id": str(item_id), "target_store_id": str(disabled.id)},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "store_unavailable"

    def test_move_item_to_unvalidated_store_rejected(self, user_client, db, user):
        """A target store at lat/lng = 0 (user_suggested, pending) yields 422."""
        store_a_id = uuid.uuid4()
        item_id = uuid.uuid4()
        s_a = Store(
            id=store_a_id,
            name="StoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        pending = Store(
            id=uuid.uuid4(),
            name="PendingStore",
            retailer="brandY",
            address="y",
            city="Paris",
            postal_code="75004",
            lat=Decimal("0"),
            lng=Decimal("0"),
        )
        db.add_all([s_a, pending])
        db.flush()
        p1 = _make_product(db, "9766677777777", "AuditProduct")

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        steps = {
            "stores": [
                {
                    "store_id": str(store_a_id),
                    "store_name": "StoreA",
                    "retailer": "brandA",
                    "address": "addr",
                    "lat": 48.857,
                    "lng": 2.352,
                    "order": 1,
                    "items": [
                        {
                            "item_id": str(item_id),
                            "product_ean": p1.ean,
                            "product_name": "AuditProduct",
                            "quantity": 1,
                            "price": 2.50,
                            "price_source": "consensus_local",
                            "trust_score": 90.0,
                        }
                    ],
                    "subtotal": 2.50,
                }
            ],
            "route_polyline": "poly",
            "total_stores": 1,
            "total_items": 1,
            "warnings": [],
        }
        route = _make_route(db, user.id, sl.id, steps=steps)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={"item_id": str(item_id), "target_store_id": str(pending.id)},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "store_unavailable"


class TestRouteStatusGuard:
    """move-item / remove-store require a 'ready' route (audit LO-07)."""

    def test_move_item_on_computing_route_rejected(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, status="computing", steps={})
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={
                "item_id": str(uuid.uuid4()),
                "target_store_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "route_not_ready"

    def test_remove_store_on_failed_route_rejected(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id, status="failed", steps={})
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "route_not_ready"


class TestRequestExtraForbid:
    """Request bodies reject unknown fields (audit LO-09, KP-84)."""

    def test_move_item_rejects_extra_field(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/move-item",
            json={
                "item_id": str(uuid.uuid4()),
                "target_store_id": str(uuid.uuid4()),
                "bogus": "x",
            },
        )
        assert resp.status_code == 422

    def test_remove_store_rejects_extra_field(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        route = _make_route(db, user.id, sl.id)
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": str(uuid.uuid4()), "bogus": "x"},
        )
        assert resp.status_code == 422


class TestOptimizeConcurrentRace:
    """Audit H7 — partial unique index prevents two 'computing' routes per list."""

    # ------------------------------------------------------------------
    # DB-level: partial unique index enforces at-most-one computing row
    # ------------------------------------------------------------------

    def test_partial_unique_index_blocks_second_computing_row(self, db, user):
        """Inserting two 'computing' rows for the same list raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        expires = datetime.now(UTC) + timedelta(hours=48)
        computed = expires - timedelta(hours=1)
        first = OptimizedRoute(
            user_id=user.id,
            list_id=sl.id,
            status="computing",
            total_price=Decimal("1.00"),
            total_savings=Decimal("0.00"),
            steps={},
            computed_at=computed,
            expires_at=expires,
        )
        db.add(first)
        db.flush()

        second = OptimizedRoute(
            user_id=user.id,
            list_id=sl.id,
            status="computing",
            total_price=Decimal("1.00"),
            total_savings=Decimal("0.00"),
            steps={},
            computed_at=computed,
            expires_at=expires,
        )
        db.add(second)
        with pytest.raises(IntegrityError):
            db.flush()

    def test_two_computing_rows_different_lists_allowed(self, db, user):
        """Two different lists can each have one 'computing' row — index is per-list."""
        sl_a = ShoppingList(user_id=user.id)
        sl_b = ShoppingList(user_id=user.id)
        db.add_all([sl_a, sl_b])
        db.flush()

        expires = datetime.now(UTC) + timedelta(hours=48)
        computed = expires - timedelta(hours=1)

        for sl in (sl_a, sl_b):
            route = OptimizedRoute(
                user_id=user.id,
                list_id=sl.id,
                status="computing",
                total_price=Decimal("1.00"),
                total_savings=Decimal("0.00"),
                steps={},
                computed_at=computed,
                expires_at=expires,
            )
            db.add(route)

        # Should not raise
        db.flush()

    def test_computing_and_ready_same_list_allowed(self, db, user):
        """A 'computing' and a 'ready' row for the same list are allowed (partial index
        only covers status='computing')."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        expires = datetime.now(UTC) + timedelta(hours=48)
        computed = expires - timedelta(hours=1)

        for status in ("computing", "ready"):
            route = OptimizedRoute(
                user_id=user.id,
                list_id=sl.id,
                status=status,
                total_price=Decimal("2.50"),
                total_savings=Decimal("0.00"),
                steps={"stores": [], "warnings": []},
                computed_at=computed,
                expires_at=expires,
            )
            db.add(route)

        # Should not raise — the partial index only fires on 'computing'
        db.flush()

    def test_computing_and_failed_same_list_allowed(self, db, user):
        """A 'computing' and a 'failed' row for the same list are allowed."""
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()

        expires = datetime.now(UTC) + timedelta(hours=48)
        computed = expires - timedelta(hours=1)

        for status in ("computing", "failed"):
            route = OptimizedRoute(
                user_id=user.id,
                list_id=sl.id,
                status=status,
                total_price=Decimal("2.50"),
                total_savings=Decimal("0.00"),
                steps={},
                computed_at=computed,
                expires_at=expires,
            )
            db.add(route)

        # Should not raise
        db.flush()

    # ------------------------------------------------------------------
    # Route-handler: IntegrityError branch returns 202 with winner's id
    # ------------------------------------------------------------------

    def test_integrity_error_branch_returns_winner_route(self, user_client, db, user, monkeypatch):
        """When the partial unique index fires (concurrent race), the handler
        catches IntegrityError, fetches the already-computing route, and returns
        202 with the winner's id — not a 500.

        Simulation: pre-insert a computing route, then force
        get_computing_route to return None on the first call (fast-path) so
        the handler proceeds to create_pending_route + commit, which triggers
        the unique violation.  The second call (in the except branch) returns
        the pre-existing route.
        """
        from unittest.mock import patch

        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        mock_task = MagicMock()
        monkeypatch.setattr("routes.optimization.task_optimize_route", mock_task)

        p1 = _make_product(db, "8111111111111", "RaceProd")
        store = _make_store(db, lat="48.857", lng="2.352", name="RaceStore")
        _make_consensus(db, store.id, p1.ean, 250)
        sl = _make_list_with_items(db, user.id, [p1])
        db.commit()

        # Pre-insert the winner's computing route
        winner = _make_route(db, user.id, sl.id, status="computing", steps={})
        db.commit()

        # Patch get_computing_route: 1st call (fast-path) → None, 2nd call
        # (IntegrityError recovery branch) → the pre-existing winner.
        call_count = {"n": 0}

        def mock_gcr(session, list_id):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # fool the fast-path check
            return winner  # recovery branch

        with patch("repositories.route_repository.get_computing_route", mock_gcr):
            resp = user_client.post(
                f"/api/v1/lists/{sl.id}/optimize",
                json={"lat": 48.856, "lng": 2.351},
            )

        assert resp.status_code == 202, resp.text
        data = resp.json()
        # Must return the winner's id, not a new one
        assert data["id"] == str(winner.id)
        assert data["status"] == "computing"
        # Task must NOT have been dispatched (the commit failed — no route was created)
        mock_task.delay.assert_not_called()


class TestRemoveStoreUnknownPriceWarning:
    """remove-store surfaces a warning when an item lands with no price (audit LO-06)."""

    def test_redistribution_to_priceless_store_warns(self, user_client, db, user):
        """Item redistributed to a store with no consensus -> 'unknown' warning."""
        store_a_id = uuid.uuid4()
        store_b_id = uuid.uuid4()
        item_id = uuid.uuid4()

        s_a = Store(
            id=store_a_id,
            name="StoreA",
            retailer="brandA",
            address="addr",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48.857"),
            lng=Decimal("2.352"),
        )
        s_b = Store(
            id=store_b_id,
            name="StoreB",
            retailer="brandB",
            address="addr2",
            city="Paris",
            postal_code="75002",
            lat=Decimal("48.860"),
            lng=Decimal("2.355"),
        )
        db.add_all([s_a, s_b])
        db.flush()

        # Product has NO consensus anywhere -> store B cannot price it.
        p1 = _make_product(db, "9799999999999", "AuditProduct")

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        steps = _two_store_steps(store_a_id, store_b_id, item_id, p1.ean)
        route = _make_route(db, user.id, sl.id, steps=steps)
        route.total_price = Decimal("6.50")
        db.commit()

        resp = user_client.post(
            f"/api/v1/routes/{route.id}/remove-store",
            json={"store_id": str(store_a_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        warnings = data["warnings"]
        assert any(w.get("type") == "unknown" and w.get("product_ean") == p1.ean for w in warnings)
