"""Tests for the optimize worker task logic (run_optimize_route called directly)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from ratis_core.models.price import PriceConsensus
from ratis_core.models.product import Product
from ratis_core.models.shopping import (
    OptimizedRoute,
    ShoppingList,
    ShoppingListItem,
)
from ratis_core.models.store import Store
from repositories import route_repository as route_repo
from services.optimization_service import run_optimize_route
from services.osrm_client import OsrmError, RouteResult, TripResult
from worker.tasks import _MAX_RETRIES, task_optimize_route

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


def _make_pending_route(db, user_id, list_id, *, status="computing"):
    route = OptimizedRoute(
        user_id=user_id,
        list_id=list_id,
        status=status,
        total_price=Decimal("0.01"),
        total_savings=Decimal("0"),
        steps={},
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db.add(route)
    db.flush()
    db.commit()
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


class _FailingOsrmClient(_MockOsrmClient):
    """OSRM client that always raises OsrmError — simulates OSRM being down."""

    def trip(self, coordinates, profile="car"):
        raise OsrmError("simulated OSRM outage")

    def route(self, origin, destination, profile="car"):
        raise OsrmError("simulated OSRM outage")


# ===========================================================================
# run_optimize_route
# ===========================================================================


class TestRunOptimizeRoute:
    """Worker: run_optimize_route sets route to 'ready' on success, 'failed' on error."""

    def test_route_finalized_on_success(self, db, user, monkeypatch):
        """Full optimization pipeline: route ends up 'ready' with populated steps."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        notify_calls: list = []
        monkeypatch.setattr(
            "services.optimization_service.notify_user",
            lambda *a, **kw: notify_calls.append(a),
        )

        p1 = _make_product(db, "1111111111111", "WorkerProd1")
        p2 = _make_product(db, "2222222222222", "WorkerProd2")
        p3 = _make_product(db, "3333333333333", "WorkerProd3")

        store = _make_store(db, lat="48.857", lng="2.352", name="WorkerStore")
        _make_consensus(db, store.id, p1.ean, 200)
        _make_consensus(db, store.id, p2.ean, 300)
        _make_consensus(db, store.id, p3.ean, 150)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        for p in [p1, p2, p3]:
            db.add(ShoppingListItem(list_id=sl.id, product_ean=p.ean, quantity=1))
        db.commit()

        route = _make_pending_route(db, user.id, sl.id)

        run_optimize_route(db, route.id, lat=48.856, lng=2.351)

        db.refresh(route)
        assert route.status == "ready"
        assert route.steps.get("stores")
        assert route.total_price > Decimal("0")

        # Notification must have been sent
        assert len(notify_calls) == 1
        assert notify_calls[0][1] == "route_ready"
        assert notify_calls[0][2]["route_id"] == str(route.id)

    def test_route_failed_no_stores_nearby(self, db, user, monkeypatch):
        """No stores within radius — worker sets route to 'failed'."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        notify_calls: list = []
        monkeypatch.setattr(
            "services.optimization_service.notify_user",
            lambda *a, **kw: notify_calls.append(a),
        )

        p1 = _make_product(db, "4444444444444", "FarProd")
        # Store far away (London)
        _make_store(db, lat="51.507", lng="-0.127", name="LondonStore")

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p1.ean, quantity=1))
        db.commit()

        route = _make_pending_route(db, user.id, sl.id)

        run_optimize_route(db, route.id, lat=48.856, lng=2.351)

        db.refresh(route)
        assert route.status == "failed"
        # No notification for failed routes
        assert len(notify_calls) == 0

    def test_route_not_found_is_noop(self, db):
        """Non-existent route_id is handled gracefully (no exception raised)."""
        run_optimize_route(db, uuid.uuid4(), lat=48.856, lng=2.351)  # should not raise

    def test_route_uses_national_average_fallback(self, db, user, monkeypatch):
        """Items with no local consensus fall back to the national average (LO-25)."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _MockOsrmClient)
        monkeypatch.setattr("services.optimization_service.notify_user", lambda *a, **kw: None)

        p1 = _make_product(db, "6111111111111", "NatAvgProd")

        # Nearby store has NO consensus for p1.
        near = _make_store(db, lat="48.857", lng="2.352", name="NearStore")
        # >=5 consensus rows at far-away stores → national average available.
        for i in range(5):
            far = _make_store(db, lat="51.50", lng="-0.12", name=f"FarStore{i}", retailer="far")
            _make_consensus(db, far.id, p1.ean, 400 + i * 10)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p1.ean, quantity=1))
        db.commit()

        route = _make_pending_route(db, user.id, sl.id)
        run_optimize_route(db, route.id, lat=48.856, lng=2.351)

        db.refresh(route)
        assert route.status == "ready"
        # The single item must be priced via the national-average fallback.
        items = route.steps["stores"][0]["items"]
        assert items[0]["price_source"] == "national_average"
        warnings = route.steps.get("warnings", [])
        assert any(w["type"] == "national_average" for w in warnings)
        # near store is used despite having no local price for the item
        assert route.steps["stores"][0]["store_id"] == str(near.id)

    def test_route_finalized_when_osrm_down(self, db, user, monkeypatch):
        """OSRM failure degrades gracefully — route still 'ready', no polyline (LO-25)."""
        monkeypatch.setattr("services.optimization_service.OsrmClient", _FailingOsrmClient)
        monkeypatch.setattr("services.optimization_service.notify_user", lambda *a, **kw: None)

        p1 = _make_product(db, "6222222222222", "OsrmDownProd")
        store = _make_store(db, lat="48.857", lng="2.352", name="OsrmStore")
        _make_consensus(db, store.id, p1.ean, 250)

        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p1.ean, quantity=1))
        db.commit()

        route = _make_pending_route(db, user.id, sl.id)
        run_optimize_route(db, route.id, lat=48.856, lng=2.351)

        db.refresh(route)
        # OsrmError is caught — route completes without a polyline.
        assert route.status == "ready"
        assert route.steps.get("route_polyline") is None
        assert route.distance_km is None
        # The OSRM failure must be surfaced to the client as a structured
        # warning so the FE can show "routing unavailable" instead of
        # silently rendering a route with no itinerary.
        warnings = route.steps.get("warnings", [])
        assert any(w["type"] == "routing_unavailable" for w in warnings)


# ===========================================================================
# task_optimize_route — Celery retry exhaustion
# ===========================================================================


class TestTaskOptimizeRouteRetry:
    """Celery task: all retries exhausted → route marked as failed."""

    def test_retry_exhaustion_marks_route_failed(self, db, user, monkeypatch):
        """After all retries are exhausted, the task marks the route as failed.

        Simulates a transient (non-OptimizationError) failure on every attempt.
        Injects retries=max_retries so the exhaustion branch is exercised.
        """
        p1 = _make_product(db, "5111111111111", "RetryProd1")
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p1.ean, quantity=1))
        db.commit()
        route = _make_pending_route(db, user.id, sl.id)

        # Transient failure on every attempt (not an OptimizationError → retried by Celery)
        def _always_fail(*a, **kw):
            raise ConnectionError("simulated DB hiccup")

        monkeypatch.setattr("services.optimization_service.run_optimize_route", _always_fail)

        # Proxy session: delegates DB ops to the test session; close() is a no-op
        # so the test session stays usable after the task's finally block.
        class _ProxySession:
            def get(self, *a, **kw):
                return db.get(*a, **kw)

            def commit(self):
                db.commit()

            def rollback(self):
                db.rollback()

            def execute(self, *a, **kw):
                return db.execute(*a, **kw)

            def close(self):
                pass  # don't close the test session

        fake_engine = MagicMock()
        monkeypatch.setattr("worker.tasks._make_session", lambda: (fake_engine, _ProxySession()))

        # Simulate: all retries already exhausted
        fake_self = MagicMock()
        fake_self.request.retries = _MAX_RETRIES
        fake_self.max_retries = _MAX_RETRIES

        # Call the task body directly, bypassing the Celery broker.
        # .run is a bound method (self=task instance) — .__func__ gives the raw function
        # so we can inject our own fake_self as the bind=True argument.
        task_optimize_route.run.__func__(fake_self, str(route.id), 48.856, 2.351)

        db.refresh(route)
        assert route.status == "failed"
        # retry() must NOT have been called — retries are exhausted
        fake_self.retry.assert_not_called()

    def test_immediate_exception_when_retry_cannot_dispatch_marks_failed(self, db, user, monkeypatch):
        """Sentry RATIS-WEBSERVICES-18 hardening — when the worker crashes
        *and* ``self.retry()`` itself cannot schedule (broker down, OOM, …),
        the outer guard MUST still leave the route ``failed``, never stuck in
        ``computing``.

        Historical bug : if the LO worker process crashed between
        ``create_pending_route + commit`` and the terminal UPDATE, the route
        stayed ``computing`` forever. The user's next optimize attempt then
        hit the partial unique index ``uq_optimized_routes_one_computing_per_list``
        → IntegrityError → manual DB intervention required. Celery's retry
        machinery is not reliable here : if the broker is the very thing that
        died, ``self.retry()`` cannot schedule anything, and the prior
        implementation would silently exit leaving the route stuck.

        The outer try/except/finally in ``task_optimize_route`` is the last
        line of defense : even if ``self.retry`` itself raises something
        non-``Retry``, the finally block marks the route ``failed``.
        """
        p1 = _make_product(db, "5222222222222", "ImmediateExcProd")
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p1.ean, quantity=1))
        db.commit()
        route = _make_pending_route(db, user.id, sl.id)

        # Service crashes on the first attempt — pre-retry-loop class of crash.
        def _crash_immediately(*a, **kw):
            raise RuntimeError("simulated crash at the very first instruction")

        monkeypatch.setattr("services.optimization_service.run_optimize_route", _crash_immediately)

        # Sentry must be called by the outer guard for observability.
        sentry_calls: list = []
        monkeypatch.setattr(
            "worker.tasks.sentry_sdk.capture_exception",
            lambda *a, **kw: sentry_calls.append(a),
        )

        class _ProxySession:
            def get(self, *a, **kw):
                return db.get(*a, **kw)

            def commit(self):
                db.commit()

            def rollback(self):
                db.rollback()

            def execute(self, *a, **kw):
                return db.execute(*a, **kw)

            def close(self):
                pass  # don't close the test session

        fake_engine = MagicMock()
        monkeypatch.setattr("worker.tasks._make_session", lambda: (fake_engine, _ProxySession()))

        # Retries NOT exhausted — but ``self.retry`` itself fails to dispatch
        # (the broker is down, the very symptom that caused the original
        # worker crash to begin with). The outer guard is the only safety net.
        fake_self = MagicMock()
        fake_self.request.retries = 0
        fake_self.max_retries = _MAX_RETRIES
        fake_self.retry.side_effect = RuntimeError("simulated broker outage")

        task_optimize_route.run.__func__(fake_self, str(route.id), 48.856, 2.351)

        db.refresh(route)
        # The route MUST be marked failed by the outer guard, not stuck in
        # 'computing'.
        assert route.status == "failed", f"route stuck in status={route.status!r} — outer guard didn't fire"
        # Sentry must have been notified so we can observe these in prod.
        assert len(sentry_calls) >= 1

    def test_celery_retry_signal_does_not_mark_failed(self, db, user, monkeypatch):
        """Counter-test of the outer guard : a *legitimate* Celery ``Retry``
        signal (retries not exhausted, broker up, retry scheduled
        successfully) must NOT mark the route ``failed``. The route stays
        ``computing`` so the scheduled retry can finish the job."""
        p1 = _make_product(db, "5333333333333", "LegitRetryProd")
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p1.ean, quantity=1))
        db.commit()
        route = _make_pending_route(db, user.id, sl.id)

        def _transient_fail(*a, **kw):
            raise ConnectionError("transient — will retry")

        monkeypatch.setattr("services.optimization_service.run_optimize_route", _transient_fail)

        class _ProxySession:
            def get(self, *a, **kw):
                return db.get(*a, **kw)

            def commit(self):
                db.commit()

            def rollback(self):
                db.rollback()

            def execute(self, *a, **kw):
                return db.execute(*a, **kw)

            def close(self):
                pass

        fake_engine = MagicMock()
        monkeypatch.setattr("worker.tasks._make_session", lambda: (fake_engine, _ProxySession()))

        from celery.exceptions import Retry

        fake_self = MagicMock()
        fake_self.request.retries = 0
        fake_self.max_retries = _MAX_RETRIES
        # Real Celery raises Retry from self.retry() on success.
        fake_self.retry.side_effect = Retry()

        # Retry MUST propagate (Celery uses it for control flow).
        import pytest

        with pytest.raises(Retry):
            task_optimize_route.run.__func__(fake_self, str(route.id), 48.856, 2.351)

        db.refresh(route)
        # Route stays computing — the scheduled retry will complete it.
        assert route.status == "computing", f"legitimate retry should not mark failed, got status={route.status!r}"

    def test_mark_route_failed_only_touches_computing(self, db, user):
        """``route_repository.mark_route_failed`` must :
        - flip ``computing`` → ``failed`` and return rowcount=1
        - never downgrade a terminal status (``ready`` / ``failed``) — guard
          via the ``AND status='computing'`` clause, return rowcount=0
        """
        sl = ShoppingList(user_id=user.id)
        db.add(sl)
        db.commit()

        # Case 1 : computing route → flipped to failed, rowcount=1
        computing = _make_pending_route(db, user.id, sl.id)
        n = route_repo.mark_route_failed(db, computing.id, reason="ghost_timeout")
        db.commit()
        assert n == 1
        db.refresh(computing)
        assert computing.status == "failed"

        # Case 2 : already-failed route → no-op, rowcount=0 (idempotency)
        n2 = route_repo.mark_route_failed(db, computing.id, reason="double_call")
        db.commit()
        assert n2 == 0

        # Case 3 : ready route → guard, never downgrade
        ready = OptimizedRoute(
            user_id=user.id,
            list_id=sl.id,
            status="ready",
            total_price=Decimal("5.00"),
            total_savings=Decimal("0"),
            steps={"stores": [], "warnings": []},
            expires_at=datetime.now(UTC) + timedelta(hours=48),
        )
        db.add(ready)
        db.commit()
        n3 = route_repo.mark_route_failed(db, ready.id, reason="should_be_noop")
        db.commit()
        assert n3 == 0
        db.refresh(ready)
        assert ready.status == "ready"

    def test_mark_route_failed_unknown_id(self, db):
        """Unknown route_id → no-op, rowcount=0 (no exception)."""
        n = route_repo.mark_route_failed(db, uuid.uuid4(), reason="nonexistent")
        db.commit()
        assert n == 0


# ===========================================================================
# Worker boot — fail-fast env validation
# ===========================================================================


class TestWorkerEnvValidation:
    """Celery worker must validate required env vars at boot (fail-fast)."""

    def test_validate_worker_env_passes_when_all_present(self, monkeypatch):
        """All required env vars present — no exception."""
        from worker.celery_app import validate_worker_env

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("OSRM_BASE_URL", "http://osrm:5000")

        validate_worker_env()  # must not raise

    def test_validate_worker_env_raises_when_osrm_missing(self, monkeypatch):
        """OSRM_BASE_URL missing — worker fails fast at boot."""
        import pytest
        from worker.celery_app import validate_worker_env

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.delenv("OSRM_BASE_URL", raising=False)

        with pytest.raises(RuntimeError, match="OSRM_BASE_URL"):
            validate_worker_env()

    def test_validate_worker_env_raises_when_database_url_missing(self, monkeypatch):
        """DATABASE_URL missing — worker fails fast at boot."""
        import pytest
        from worker.celery_app import validate_worker_env

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("OSRM_BASE_URL", "http://osrm:5000")

        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            validate_worker_env()
