from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from ratis_core.models.shopping import OptimizedRoute, ShoppingList
from ratis_core.models.store import Store
from ratis_core.settings import load_settings
from ratis_core.utils import assert_owner
from repositories import route_repository as route_repo
from repositories import shopping_list_repository as list_repo
from services.optimization_service import (
    EmptyList,
    NoPosition,
    create_pending_route,
    validate_for_optimization,
)
from services.price_service import resolve_price
from services.route_mutation_service import (
    CannotRemoveLastStore,
    ItemNotFoundInRoute,
    StoreNotFound,
    StoreUnavailable,
    move_item_in_route,
    remove_store_from_route,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from worker.tasks import task_optimize_route

logger = logging.getLogger(__name__)

router = APIRouter(tags=["optimization"])


# -- Schemas ---------------------------------------------------------------


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float | None = None
    lng: float | None = None


class MoveItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    target_store_id: uuid.UUID


class RemoveStoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: uuid.UUID


# -- Helpers ---------------------------------------------------------------


def _slim_response(route: OptimizedRoute) -> dict:
    """Return minimal route data for computing/updating/failed states."""
    return {
        "id": str(route.id),
        "list_id": str(route.list_id),
        "status": route.status,
    }


def format_route_response(route: OptimizedRoute) -> dict:
    """Format a ready OptimizedRoute for JSON response."""
    steps = route.steps
    return {
        "id": str(route.id),
        "list_id": str(route.list_id),
        "status": route.status,
        "total_price": float(route.total_price),
        "total_savings": float(route.total_savings),
        "distance_km": float(route.distance_km) if route.distance_km else None,
        "computed_at": route.computed_at.isoformat(),
        "expires_at": route.expires_at.isoformat(),
        "stores": steps.get("stores", []),
        "route_polyline": steps.get("route_polyline"),
        "warnings": steps.get("warnings", []),
    }


def _route_response(route: OptimizedRoute) -> dict:
    """Return slim response for non-ready routes, full response for ready."""
    if route.status == "ready":
        return format_route_response(route)
    return _slim_response(route)


def _load_owned_route(
    db: Session,
    route_id: uuid.UUID,
    user,
    *,
    check_expiry: bool = True,
    for_update: bool = False,
) -> OptimizedRoute:
    """Load a route, asserting ownership and (optionally) freshness.

    When ``for_update`` is set the row is fetched with a write lock so that
    concurrent JSONB ``steps`` mutations serialize.

    Raises HTTPException 404 if missing, 403 if not owned, 410 if expired.
    """
    if for_update:
        route = route_repo.get_route_for_update(db, route_id)
    else:
        route = route_repo.get_route(db, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="route_not_found")
    assert_owner(route, user.id)
    if check_expiry and route_repo.is_expired(route):
        raise HTTPException(status_code=410, detail="route_expired")
    return route


def _load_owned_list(db: Session, list_id: uuid.UUID, user) -> ShoppingList:
    """Load a shopping list, asserting ownership.

    Raises HTTPException 404 if missing, 403 if not owned.
    """
    sl = db.get(ShoppingList, list_id)
    if sl is None:
        raise HTTPException(status_code=404, detail="list_not_found")
    assert_owner(sl, user.id)
    return sl


# -- Existing endpoint -----------------------------------------------------


@router.get("/price")
def get_price(
    product_ean: str = Query(..., max_length=20, pattern=r"^\d{8,14}$"),
    store_id: uuid.UUID = Query(...),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    get_http_current_user(db, token)  # auth gate only — caller identity unused
    if db.get(Store, store_id) is None:
        raise HTTPException(status_code=404, detail="store_not_found")
    result = resolve_price(db, product_ean, store_id)
    resp: dict = {
        "product_ean": result.product_ean,
        "store_id": str(result.store_id),
        "price": result.price,
        "price_source": result.price_source,
        "trust_score": result.trust_score,
    }
    if result.warning:
        resp["warning"] = result.warning
    return resp


# -- Trigger optimization (async) ------------------------------------------


@router.post("/lists/{list_id}/optimize", status_code=202)
def trigger_optimization(
    list_id: uuid.UUID,
    body: OptimizeRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Queue route optimization for a shopping list.

    Sync pre-checks (fast): list ownership, is_template, EmptyList, NoPosition.
    Everything else (NoStoresNearby, OSRM, price matrix) happens in the worker.
    Returns 202 with {id, status: "computing"}.
    """
    user = get_http_current_user(db, token)

    sl = _load_owned_list(db, list_id, user)

    if sl.is_template:
        raise HTTPException(status_code=422, detail="cannot_optimize_template")

    # Reject oversized lists before building the (expensive) price matrix.
    cfg = load_settings().get("list_optimiser", {})
    max_items = cfg["max_items_per_list"]
    if list_repo.count_items(db, list_id) > max_items:
        raise HTTPException(status_code=422, detail="list_too_large")

    try:
        lat, lng = validate_for_optimization(db, user.id, list_id, body.lat, body.lng)
    except EmptyList:
        raise HTTPException(status_code=422, detail="empty_list")
    except NoPosition:
        raise HTTPException(status_code=422, detail="no_position")

    # Idempotency guard — a rapid double-tap must not spawn a second
    # 'computing' route + Celery task for the same list. Return the
    # in-flight route instead.
    #
    # Ghost-row hardening (Sentry RATIS-WEBSERVICES-18) : if the existing
    # 'computing' row is older than ``stuck_computing_threshold_minutes``,
    # the worker most likely crashed between INSERT(computing) and the
    # terminal UPDATE (e.g. LO restart, broker hiccup, OOM). Without this
    # reset the user is stuck forever — the partial unique index
    # ``uq_optimized_routes_one_computing_per_list`` blocks any new attempt
    # and requires manual DB intervention. Mark the ghost ``failed`` and
    # fall through to creating a fresh row.
    existing = route_repo.get_computing_route(db, list_id)
    if existing is not None:
        threshold_min = cfg.get("stuck_computing_threshold_minutes", 10)
        cutoff = datetime.now(UTC) - timedelta(minutes=threshold_min)
        if existing.computed_at < cutoff:
            logger.warning(
                "Detected ghost 'computing' route %s for list %s "
                "(computed_at=%s, threshold=%dmin) — resetting and recomputing",
                existing.id,
                list_id,
                existing.computed_at,
                threshold_min,
            )
            sentry_sdk.add_breadcrumb(
                category="list_optimiser",
                level="warning",
                message="ghost_computing_route_reset",
                data={
                    "route_id": str(existing.id),
                    "list_id": str(list_id),
                    "age_minutes": (datetime.now(UTC) - existing.computed_at).total_seconds() / 60,
                    "threshold_minutes": threshold_min,
                },
            )
            n = route_repo.mark_route_failed(db, existing.id, reason="ghost_timeout")
            db.commit()
            if n == 0:
                # Race : another worker / request just terminated it. Re-check
                # — if a fresh computing now exists, return it; else fall
                # through and create a new one.
                refreshed = route_repo.get_computing_route(db, list_id)
                if refreshed is not None and refreshed.id != existing.id:
                    return JSONResponse(
                        content={"id": str(refreshed.id), "status": "computing"},
                        status_code=202,
                    )
            existing = None  # fall through to create a fresh route
        else:
            return JSONResponse(
                content={"id": str(existing.id), "status": "computing"},
                status_code=202,
            )

    try:
        route = create_pending_route(db, user.id, list_id)
        db.commit()
    except IntegrityError:
        # A concurrent request created the 'computing' route first — the
        # partial unique index uq_optimized_routes_one_computing_per_list
        # rejected this one. Return the in-flight route, not a 500.
        db.rollback()
        existing = route_repo.get_computing_route(db, list_id)
        if existing is not None:
            return JSONResponse(
                content={"id": str(existing.id), "status": "computing"},
                status_code=202,
            )
        raise  # genuinely unexpected — re-raise

    try:
        task_optimize_route.delay(str(route.id), lat, lng)
    except Exception:
        # Enqueue failed (e.g. Redis broker down). Without this guard the
        # route stays 'computing' forever — a permanent stuck state with no
        # failure signal. Mark it 'failed' so the client can surface an error
        # and retry, and report the enqueue failure for investigation.
        logger.exception("Failed to enqueue optimize task for route %s — marking failed", route.id)
        sentry_sdk.capture_exception()
        route.status = "failed"
        db.commit()

    return JSONResponse(
        content={"id": str(route.id), "status": "computing"},
        status_code=202,
    )


# -- Get route by ID -------------------------------------------------------


@router.get("/routes/{route_id}")
def get_route_detail(
    route_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Get an optimized route by its ID.

    Returns slim response {id, list_id, status} while computing/updating/failed.
    Returns full response when ready.
    """
    user = get_http_current_user(db, token)
    route = _load_owned_route(db, route_id, user)
    return _route_response(route)


# -- Get latest route for a list -------------------------------------------


@router.get("/lists/{list_id}/route")
def get_latest_route(
    list_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Get the latest non-expired route for a shopping list."""
    user = get_http_current_user(db, token)
    _load_owned_list(db, list_id, user)
    route = route_repo.get_latest_route(db, list_id)
    if route is None:
        raise HTTPException(status_code=404, detail="no_active_route")
    return _route_response(route)


# -- Move item between stores (sync) ---------------------------------------


@router.post("/routes/{route_id}/move-item")
def move_item(
    route_id: uuid.UUID,
    body: MoveItemRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Move an item from one store to another within an optimized route.

    Synchronous — returns updated route immediately (< 100 ms).
    """
    user = get_http_current_user(db, token)
    route = _load_owned_route(db, route_id, user, for_update=True)
    if route.status != "ready":
        raise HTTPException(status_code=409, detail="route_not_ready")

    try:
        move_item_in_route(route, str(body.item_id), str(body.target_store_id), db)
    except ItemNotFoundInRoute:
        raise HTTPException(status_code=404, detail="item_not_found_in_route")
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="store_not_found")
    except StoreUnavailable:
        raise HTTPException(status_code=422, detail="store_unavailable")

    db.commit()
    db.refresh(route)
    return format_route_response(route)


# -- Remove store from route (sync) ----------------------------------------


@router.post("/routes/{route_id}/remove-store")
def remove_store(
    route_id: uuid.UUID,
    body: RemoveStoreRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Remove a store from the route and redistribute its items.

    Synchronous — returns updated route immediately (< 100 ms).
    """
    user = get_http_current_user(db, token)
    route = _load_owned_route(db, route_id, user, for_update=True)
    if route.status != "ready":
        raise HTTPException(status_code=409, detail="route_not_ready")

    try:
        remove_store_from_route(route, str(body.store_id), db)
    except CannotRemoveLastStore:
        raise HTTPException(status_code=422, detail="cannot_remove_last_store")
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="store_not_found")

    db.commit()
    db.refresh(route)
    return format_route_response(route)
