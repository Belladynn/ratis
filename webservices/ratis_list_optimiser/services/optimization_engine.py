"""
Core optimization engine -- assigns items to stores based on price matrix.

Pure function with no side effects. Takes pre-fetched data, returns assignments.
The worker/orchestrator handles DB queries, OSRM calls, and persistence.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass
class ItemPrice:
    """Price data for a (store, product) pair."""

    price: Decimal | None
    source: str  # "consensus_local" | "national_average" | "unknown"
    trust_score: Decimal | None


@dataclass
class StoreAssignment:
    """Assignment result for a single item."""

    store_id: uuid.UUID
    price: Decimal | None
    source: str
    trust_score: Decimal | None


def assign_items_to_stores(
    items: list[str],
    stores: list[uuid.UUID],
    prices: dict[tuple[uuid.UUID, str], ItemPrice],
    min_items_per_store: int = 3,
) -> dict[str, StoreAssignment]:
    """
    Assign each item (product_ean) to the cheapest store.
    Then apply the threshold: stores with < min_items_per_store items
    get their items redistributed to qualifying stores.

    Args:
        items: list of product_ean strings
        stores: list of store UUIDs (nearby, not disabled, not excluded)
        prices: dict mapping (store_id, product_ean) -> ItemPrice
        min_items_per_store: minimum items for a store to be worth visiting

    Returns:
        dict mapping product_ean -> StoreAssignment
    """
    if not items:
        return {}

    # Step 1: initial assignment -- each item to cheapest store
    assignments: dict[str, StoreAssignment] = {}

    for ean in items:
        best_store = None
        best_price_data = None

        for store_id in stores:
            key = (store_id, ean)
            if (
                key in prices
                and prices[key].price is not None
                and (best_price_data is None or prices[key].price < best_price_data.price)
            ):
                best_store = store_id
                best_price_data = prices[key]

        if best_store is not None:
            assignments[ean] = StoreAssignment(
                store_id=best_store,
                price=best_price_data.price,
                source=best_price_data.source,
                trust_score=best_price_data.trust_score,
            )
        else:
            # No price at any store -- assign to first store with unknown price
            fallback_store = stores[0] if stores else None
            assignments[ean] = StoreAssignment(
                store_id=fallback_store,
                price=None,
                source="unknown",
                trust_score=None,
            )

    # Step 2: redistribute under-threshold stores
    assignments = redistribute_under_threshold(assignments, prices, min_items_per_store)

    return assignments


def redistribute_under_threshold(
    assignments: dict[str, StoreAssignment],
    prices: dict[tuple[uuid.UUID, str], ItemPrice],
    min_items_per_store: int,
) -> dict[str, StoreAssignment]:
    """
    Iteratively remove stores that have fewer than min_items_per_store items.
    Reassign their items to the cheapest qualifying store.
    """
    changed = True
    while changed:
        changed = False

        # Count items per store
        store_counts: dict[uuid.UUID, int] = defaultdict(int)
        for assignment in assignments.values():
            if assignment.store_id is not None:
                store_counts[assignment.store_id] += 1

        # Find stores meeting the threshold
        qualifying = {sid for sid, count in store_counts.items() if count >= min_items_per_store}
        under = {sid for sid, count in store_counts.items() if count < min_items_per_store}

        if not under:
            break

        # If no qualifying stores exist, we cannot redistribute -- stop
        if not qualifying:
            break

        # Pick the store with fewest items to remove first
        worst_store = min(under, key=lambda s: store_counts[s])

        # Reassign its items
        for ean, assignment in assignments.items():
            if assignment.store_id != worst_store:
                continue

            # Find cheapest price among qualifying stores
            best_store = None
            best_price = None
            for qstore in qualifying:
                key = (qstore, ean)
                if (
                    key in prices
                    and prices[key].price is not None
                    and (best_price is None or prices[key].price < best_price)
                ):
                    best_store = qstore
                    best_price = prices[key].price

            if best_store is not None:
                assignments[ean] = StoreAssignment(
                    store_id=best_store,
                    price=best_price,
                    source=prices[(best_store, ean)].source,
                    trust_score=prices[(best_store, ean)].trust_score,
                )
            # else: keep at worst_store (no alternative available)

        changed = True

    return assignments


def cap_to_max_stores(
    assignments: dict[str, StoreAssignment],
    prices: dict[tuple[uuid.UUID, str], ItemPrice],
    max_stores: int,
) -> dict[str, StoreAssignment]:
    """Cap the route to at most ``max_stores`` distinct stores.

    The stores holding the most items are kept (the most relevant per the
    already-computed assignment); items assigned to dropped stores are
    reassigned to the cheapest kept store that has a price for them, falling
    back to the largest kept store when no kept store prices the item.

    Args:
        assignments: product_ean -> StoreAssignment (post-redistribution).
        prices: (store_id, product_ean) -> ItemPrice price matrix.
        max_stores: maximum number of distinct stores allowed in the route.

    Returns:
        A new assignments dict with at most ``max_stores`` distinct stores.
    """
    # Count items per store
    store_counts: dict[uuid.UUID, int] = defaultdict(int)
    for assignment in assignments.values():
        if assignment.store_id is not None:
            store_counts[assignment.store_id] += 1

    if len(store_counts) <= max_stores:
        return assignments

    # Keep the largest stores (ties broken deterministically by store id).
    ranked = sorted(store_counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    kept = {sid for sid, _ in ranked[:max_stores]}

    # Largest kept store — fallback target when no kept store prices an item.
    fallback_store = ranked[0][0]

    capped: dict[str, StoreAssignment] = {}
    for ean, assignment in assignments.items():
        if assignment.store_id in kept:
            capped[ean] = assignment
            continue

        # Item's store was dropped — reassign to cheapest kept store.
        best_store = None
        best_price = None
        for kstore in kept:
            key = (kstore, ean)
            if (
                key in prices
                and prices[key].price is not None
                and (best_price is None or prices[key].price < best_price)
            ):
                best_store = kstore
                best_price = prices[key].price

        if best_store is not None:
            capped[ean] = StoreAssignment(
                store_id=best_store,
                price=best_price,
                source=prices[(best_store, ean)].source,
                trust_score=prices[(best_store, ean)].trust_score,
            )
        else:
            # No kept store prices this item — park it at the largest store.
            capped[ean] = StoreAssignment(
                store_id=fallback_store,
                price=None,
                source="unknown",
                trust_score=None,
            )

    return capped
