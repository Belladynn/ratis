"""Route optimization orchestrator."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ratis_core.models.analytics import UserPreferences
from ratis_core.models.product import Product
from ratis_core.models.shopping import (
    OptimizedRoute,
    ShoppingListItem,
    UserStorePreference,
)
from ratis_core.models.user import User
from ratis_core.notifier_client import notify_user
from ratis_core.settings import load_settings
from repositories import price_repository
from sqlalchemy import select
from sqlalchemy.orm import Session

from ratis_core import geo
from services.optimization_engine import (
    ItemPrice,
    assign_items_to_stores,
    cap_to_max_stores,
)
from services.osrm_client import OsrmClient, OsrmError

logger = logging.getLogger(__name__)

# Minimum value for OptimizedRoute.total_price — the DB enforces a
# ``total_price > 0`` CHECK constraint, so a genuinely-zero route total is
# floored to this sentinel rather than violating the constraint.
MIN_TOTAL_PRICE = Decimal("0.01")


class OptimizationError(Exception):
    pass


class EmptyList(OptimizationError):
    pass


class NoPosition(OptimizationError):
    pass


class NoStoresNearby(OptimizationError):
    pass


# ---------------------------------------------------------------------------
# Sync pre-check — called from the route handler before dispatching the task
# ---------------------------------------------------------------------------


def validate_for_optimization(
    db: Session,
    user_id: uuid.UUID,
    list_id: uuid.UUID,
    lat: float | None = None,
    lng: float | None = None,
) -> tuple[float, float]:
    """Verify the list has unchecked items and position is resolvable.

    Returns (lat, lng) — may be filled from user.ref_lat/ref_lng.

    Raises:
        EmptyList: list has no unchecked items.
        NoPosition: no lat/lng provided and user has no ref_lat/ref_lng.
    """
    items_q = select(ShoppingListItem).where(
        ShoppingListItem.list_id == list_id,
        ShoppingListItem.checked == False,  # noqa: E712
    )
    items = list(db.scalars(items_q).all())
    if not items:
        raise EmptyList()

    if lat is None or lng is None:
        user = db.get(User, user_id)
        if user is None or user.ref_lat is None or user.ref_lng is None:
            raise NoPosition()
        lat = float(user.ref_lat)
        lng = float(user.ref_lng)

    return lat, lng


# ---------------------------------------------------------------------------
# Pending route creation — called from the route handler
# ---------------------------------------------------------------------------


def create_pending_route(
    db: Session,
    user_id: uuid.UUID,
    list_id: uuid.UUID,
) -> OptimizedRoute:
    """Insert a placeholder route with status='computing'.

    The worker will finalize it with full data once computation completes.
    """
    cfg = load_settings().get("list_optimiser", {})
    expiry_hours = cfg.get("route_expiry_hours", 48)

    route = OptimizedRoute(
        user_id=user_id,
        list_id=list_id,
        status="computing",
        total_price=Decimal("0.01"),  # CHECK constraint requires > 0
        total_savings=Decimal("0"),
        steps={},
        expires_at=datetime.now(UTC) + timedelta(hours=expiry_hours),
    )
    db.add(route)
    db.flush()
    return route


# ---------------------------------------------------------------------------
# Core computation — pure (no DB writes)
# ---------------------------------------------------------------------------


def _compute_route_data(
    db: Session,
    user_id: uuid.UUID,
    list_id: uuid.UUID,
    lat: float,
    lng: float,
) -> dict:
    """Run the full optimization pipeline and return a dict of computed data.

    Raises:
        OptimizationError / subclasses on failure (e.g. NoStoresNearby).
    """
    cfg = load_settings().get("list_optimiser", {})
    min_items = cfg.get("min_items_per_store", 3)
    osrm_timeout = cfg.get("osrm_timeout_seconds", 10)
    min_national = cfg.get("national_avg_min_datapoints", 5)
    max_stores = cfg["max_stores_in_route"]

    # 1. Load unchecked items
    items_q = select(ShoppingListItem).where(
        ShoppingListItem.list_id == list_id,
        ShoppingListItem.checked == False,  # noqa: E712
    )
    items = list(db.scalars(items_q).all())
    if not items:
        raise EmptyList()

    # 2. User preferences — fall back to configured defaults (R19) when the
    # user has no UserPreferences row.
    prefs = db.get(UserPreferences, user_id)
    radius_km = prefs.search_radius_km if prefs else cfg["default_search_radius_km"]
    transport = prefs.transport_mode if prefs else cfg["default_transport_mode"]

    # 3. Excluded stores
    excluded_q = select(UserStorePreference.store_id).where(
        UserStorePreference.user_id == user_id,
        UserStorePreference.preference == "excluded",
    )
    excluded_ids = set(db.scalars(excluded_q).all())

    # 4. Find nearby stores (PostGIS — ratis_core.geo)
    nearby = [p.store for p in geo.stores_within_radius(db, lat, lng, radius_km, exclude_store_ids=excluded_ids)]
    if not nearby:
        raise NoStoresNearby()

    # 5. Build price matrix
    store_ids = [s.id for s in nearby]
    item_eans = [it.product_ean for it in items]

    prices: dict[tuple[uuid.UUID, str], ItemPrice] = {}
    for sid in store_ids:
        for ean in item_eans:
            local = price_repository.get_local_price(db, sid, ean)
            if local is not None:
                price_cents, trust = local
                if price_cents > 0:
                    prices[(sid, ean)] = ItemPrice(
                        price=Decimal(price_cents) / 100,
                        source="consensus_local",
                        trust_score=trust,
                    )

    # National average fallback for items with no local price at any store
    for ean in item_eans:
        has_local = any((sid, ean) in prices for sid in store_ids)
        if not has_local:
            national = price_repository.get_national_average(db, ean)
            if national is not None:
                avg_cents, count = national
                if count >= min_national and avg_cents > 0:
                    for sid in store_ids:
                        prices[(sid, ean)] = ItemPrice(
                            price=Decimal(avg_cents) / 100,
                            source="national_average",
                            trust_score=None,
                        )

    # 6. Assign items to stores, then cap the route to max_stores_in_route.
    # The cap keeps the most relevant (largest) stores per the assignment
    # and reassigns items from dropped stores to the cheapest kept store.
    assignments = assign_items_to_stores(item_eans, store_ids, prices, min_items)
    assignments = cap_to_max_stores(assignments, prices, max_stores)

    # 7. OSRM routing
    assigned_stores = list({a.store_id for a in assignments.values() if a.store_id is not None})
    store_map = {s.id: s for s in nearby}

    route_polyline = None
    distance_km = None
    store_order = assigned_stores  # default order
    warnings: list[dict] = []

    # No default — fail-fast (R20). The web process validates this in its
    # lifespan; the Celery worker validates it at boot (worker/celery_app.py).
    osrm_base = os.environ["OSRM_BASE_URL"]
    osrm = OsrmClient(base_url=osrm_base, timeout=osrm_timeout)
    profile = osrm.map_transport_mode(transport)

    if len(assigned_stores) >= 2:
        try:
            coords = [(lng, lat)]  # OSRM uses lng, lat
            for sid in assigned_stores:
                s = store_map[sid]
                coords.append((float(s.lng), float(s.lat)))

            trip = osrm.trip(coords, profile=profile)
            route_polyline = trip.geometry
            distance_km = round(trip.distance_m / 1000, 2)

            wp_order = trip.waypoint_order
            store_indices = [(wp_order[i], assigned_stores[i - 1]) for i in range(1, len(wp_order))]
            store_indices.sort(key=lambda x: x[0])
            store_order = [sid for _, sid in store_indices]
        except OsrmError:
            logger.warning("OSRM failed for route optimization — continuing without polyline")
            warnings.append({"type": "routing_unavailable"})

    elif len(assigned_stores) == 1:
        try:
            sid = assigned_stores[0]
            s = store_map[sid]
            result = osrm.route((lng, lat), (float(s.lng), float(s.lat)), profile=profile)
            route_polyline = result.geometry
            distance_km = round(result.distance_m / 1000, 2)
        except OsrmError:
            logger.warning("OSRM failed for single-store route — continuing without polyline")
            warnings.append({"type": "routing_unavailable"})

    # 8. Build JSONB steps (no departure point — PII)
    item_map = {it.product_ean: it for it in items}

    # Batch the product-name lookup — one query instead of one per EAN.
    name_rows = db.execute(select(Product.ean, Product.name).where(Product.ean.in_(item_eans))).all()
    product_names: dict[str, str] = dict(name_rows)

    stores_data: list[dict] = []
    total_price_cents = 0

    for order_idx, sid in enumerate(store_order, 1):
        s = store_map.get(sid)
        if s is None:
            continue

        store_items: list[dict] = []
        subtotal_cents = 0

        for ean, assignment in assignments.items():
            if assignment.store_id != sid:
                continue

            item_obj = item_map.get(ean)
            price_euros = float(assignment.price) if assignment.price is not None else None
            price_cents = round(assignment.price * 100) if assignment.price is not None else None

            store_items.append(
                {
                    "item_id": str(item_obj.id) if item_obj else None,
                    "product_ean": ean,
                    "product_name": product_names.get(ean, ean),
                    "quantity": float(item_obj.quantity) if item_obj else 1,
                    "price": price_euros,
                    "price_source": assignment.source,
                    "trust_score": (float(assignment.trust_score) if assignment.trust_score is not None else None),
                }
            )

            if price_cents is not None and item_obj:
                subtotal_cents += round(price_cents * float(item_obj.quantity))

            if assignment.source == "national_average":
                warnings.append({"product_ean": ean, "type": "national_average"})
            elif assignment.source == "unknown":
                warnings.append({"product_ean": ean, "type": "unknown"})

        stores_data.append(
            {
                "store_id": str(sid),
                "store_name": s.name,
                "retailer": s.retailer,
                "address": s.address,
                "lat": float(s.lat),
                "lng": float(s.lng),
                "order": order_idx,
                "items": store_items,
                "subtotal": round(subtotal_cents / 100, 2),
            }
        )

        total_price_cents += subtotal_cents

    steps = {
        "stores": stores_data,
        "route_polyline": route_polyline,
        "total_stores": len(stores_data),
        "total_items": len(items),
        "warnings": warnings,
    }

    total_price = max(Decimal(str(round(total_price_cents / 100, 2))), MIN_TOTAL_PRICE)

    return {
        "total_price": total_price,
        "total_savings": Decimal("0"),  # V2 feature
        "distance_km": Decimal(str(distance_km)) if distance_km else None,
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Finalize — write computed data back to route
# ---------------------------------------------------------------------------


def _finalize_route(db: Session, route: OptimizedRoute, data: dict) -> None:
    """Update route with computed data and mark it as 'ready'."""
    route.total_price = data["total_price"]
    route.total_savings = data["total_savings"]
    route.distance_km = data["distance_km"]
    route.steps = data["steps"]
    route.status = "ready"
    db.flush()


# ---------------------------------------------------------------------------
# Worker callable — called by the Celery task with a fresh DB session
# ---------------------------------------------------------------------------


def run_optimize_route(
    db: Session,
    route_id: uuid.UUID,
    lat: float,
    lng: float,
) -> None:
    """Compute route data and finalize the pending route. Called by the Celery worker.

    On success: route.status = "ready", notify_user("route_ready").
    On failure: route.status = "failed", no notification.
    """
    route = db.get(OptimizedRoute, route_id)
    if route is None:
        logger.error("run_optimize_route: route %s not found — skipping", route_id)
        return

    try:
        data = _compute_route_data(db, route.user_id, route.list_id, lat, lng)
        _finalize_route(db, route, data)
        db.commit()
        notify_user(
            route.user_id,
            "route_ready",
            {"route_id": str(route_id)},
        )
    except OptimizationError as exc:
        # Permanent failure (e.g. NoStoresNearby) — no retry needed
        logger.warning("run_optimize_route: permanent failure for route %s: %s", route_id, exc)
        route.status = "failed"
        db.commit()
    # Transient exceptions (DB hiccup, OSRM temporarily down) propagate
    # so the Celery task can retry before giving up
