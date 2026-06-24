"""Route mutation operations: move item between stores, remove a store."""

from __future__ import annotations

import uuid
from decimal import Decimal

from ratis_core.models.shopping import OptimizedRoute
from ratis_core.models.store import Store
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from services.optimization_service import MIN_TOTAL_PRICE
from services.price_service import resolve_price


class ItemNotFoundInRoute(Exception):
    pass


class StoreNotFound(Exception):
    pass


class StoreUnavailable(Exception):
    """Target store exists but is disabled or has no validated coordinates."""

    pass


class CannotRemoveLastStore(Exception):
    pass


def _assert_store_usable(store: Store) -> None:
    """Reject a target store that is soft-deleted or not yet geo-validated.

    A ``user_suggested`` store sits at lat/lng = 0 until an admin validates
    it; routing to it would produce a nonsensical itinerary.

    Raises:
        StoreUnavailable: store is disabled or has placeholder coordinates.
    """
    if store.is_disabled or (store.lat == 0 and store.lng == 0):
        raise StoreUnavailable(f"Store {store.id} is unavailable")


def move_item_in_route(
    route: OptimizedRoute,
    item_id: str,
    target_store_id: str,
    db: Session,
) -> None:
    """Move an item from its current store to a target store within the steps JSONB.

    Raises:
        ItemNotFoundInRoute: if item_id is not found in any store in the route.
        StoreNotFound: if target_store_id does not exist in DB.
        StoreUnavailable: if the target store is disabled or not geo-validated.
    """
    steps = route.steps
    stores = steps.get("stores", [])

    # Find the item and its source store
    source_store = None
    item_data = None
    for store in stores:
        for item in store["items"]:
            if item["item_id"] == item_id:
                source_store = store
                item_data = item
                break
        if item_data:
            break

    if item_data is None:
        raise ItemNotFoundInRoute(f"Item {item_id} not found in route {route.id}")

    # Validate the target store before mutating ``steps`` — fail fast so a
    # disabled or not-yet-geo-validated store never enters the route.
    target = db.get(Store, uuid.UUID(target_store_id))
    if target is None:
        raise StoreNotFound(f"Store {target_store_id} not found")
    _assert_store_usable(target)

    # Resolve price at target store
    price_result = resolve_price(db, item_data["product_ean"], uuid.UUID(target_store_id))
    item_data["price"] = price_result.price
    item_data["price_source"] = price_result.price_source
    item_data["trust_score"] = price_result.trust_score

    # Remove item from source store
    source_store["items"] = [i for i in source_store["items"] if i["item_id"] != item_id]

    # Find or create target store entry
    target_store = None
    for store in stores:
        if store["store_id"] == target_store_id:
            target_store = store
            break

    if target_store is None:
        target_store = {
            "store_id": target_store_id,
            "store_name": target.name,
            "retailer": target.retailer,
            "address": target.address,
            "lat": float(target.lat),
            "lng": float(target.lng),
            "order": len(stores) + 1,
            "items": [],
            "subtotal": 0,
        }
        stores.append(target_store)

    target_store["items"].append(item_data)

    # Remove stores that became empty and sync the counter
    steps["stores"] = [s for s in stores if s["items"]]
    steps["total_stores"] = len(steps["stores"])

    _recalculate_totals(route)
    flag_modified(route, "steps")


def remove_store_from_route(
    route: OptimizedRoute,
    store_id: str,
    db: Session,
) -> None:
    """Remove a store from the route and redistribute its items to remaining stores.

    Raises:
        CannotRemoveLastStore: the route has only one store left.
        StoreNotFound: store_id is not part of the route.
    """
    steps = route.steps
    stores = steps.get("stores", [])

    if len(stores) <= 1:
        raise CannotRemoveLastStore(f"Route {route.id} has only one store")

    store_to_remove = None
    for store in stores:
        if store["store_id"] == store_id:
            store_to_remove = store
            break

    if store_to_remove is None:
        raise StoreNotFound(f"Store {store_id} not in route {route.id}")

    items_to_move = store_to_remove["items"]
    remaining_stores = [s for s in stores if s["store_id"] != store_id]
    warnings = steps.setdefault("warnings", [])

    for item in items_to_move:
        ean = item["product_ean"]
        best_store = None
        best_price_result = None

        for rs in remaining_stores:
            rs_id = uuid.UUID(rs["store_id"])
            pr = resolve_price(db, ean, rs_id)
            if pr.price is not None and (best_price_result is None or pr.price < best_price_result.price):
                best_store = rs
                best_price_result = pr

        if best_store is None:
            best_store = remaining_stores[0]
            best_price_result = resolve_price(db, ean, uuid.UUID(best_store["store_id"]))

        item["price"] = best_price_result.price
        item["price_source"] = best_price_result.price_source
        item["trust_score"] = best_price_result.trust_score
        best_store["items"].append(item)

        # No store in the route has a price for this item after
        # redistribution — surface it so the FE can flag the row instead
        # of silently dropping the item from the displayed total.
        if best_price_result.price is None:
            warnings.append({"product_ean": ean, "type": "unknown"})

    steps["stores"] = remaining_stores

    # Reorder
    for idx, store in enumerate(steps["stores"], 1):
        store["order"] = idx

    steps["total_stores"] = len(steps["stores"])

    _recalculate_totals(route)
    flag_modified(route, "steps")


def _recalculate_totals(route: OptimizedRoute) -> None:
    """Recalculate subtotals per store and the overall total_price."""
    total_cents = 0
    for store in route.steps.get("stores", []):
        subtotal_cents = 0
        for item in store["items"]:
            if item.get("price") is not None:
                qty = item.get("quantity", 1)
                subtotal_cents += round(Decimal(str(item["price"])) * Decimal(str(qty)) * 100)
        store["subtotal"] = round(subtotal_cents / 100, 2)
        total_cents += subtotal_cents

    route.total_price = max(Decimal(str(round(total_cents / 100, 2))), MIN_TOTAL_PRICE)
