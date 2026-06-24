"""Progressive shopping list suggestions based on purchase history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ratis_core.models.product import Product
from ratis_core.models.scan import Scan
from ratis_core.models.shopping import ShoppingList
from ratis_core.settings import load_settings
from ratis_core.utils import assert_owner
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from services import shopping_list_service as list_svc


class NotEligible(Exception):
    pass


class ListNotFound(Exception):
    pass


@dataclass
class SuggestedProduct:
    product_ean: str
    product_name: str
    frequency: float
    appearances: int
    total_receipts: int


def count_user_receipts(db: Session, user_id: uuid.UUID) -> int:
    """Count distinct receipts with accepted scans for a user."""
    stmt = select(func.count(distinct(Scan.receipt_id))).where(
        Scan.user_id == user_id,
        Scan.scan_type == "receipt",
        Scan.status == "accepted",
        Scan.receipt_id.isnot(None),
    )
    return db.scalar(stmt) or 0


def check_eligibility(db: Session, user_id: uuid.UUID) -> dict:
    """Check whether a user has enough receipts for suggestions."""
    cfg = load_settings().get("list_optimiser", {})
    min_receipts = cfg.get("suggestion_min_receipts", 3)
    count = count_user_receipts(db, user_id)
    return {
        "eligible": count >= min_receipts,
        "receipt_count": count,
        "min_required": min_receipts,
    }


def suggest_products(db: Session, user_id: uuid.UUID) -> list[SuggestedProduct]:
    """Generate product suggestions based on purchase frequency."""
    cfg = load_settings().get("list_optimiser", {})
    min_receipts = cfg.get("suggestion_min_receipts", 3)
    threshold = cfg.get("suggestion_frequency_threshold", 0.50)

    total = count_user_receipts(db, user_id)
    if total < min_receipts:
        raise NotEligible()

    stmt = (
        select(
            Scan.product_ean,
            func.count(distinct(Scan.receipt_id)).label("appearances"),
        )
        .where(
            Scan.user_id == user_id,
            Scan.scan_type == "receipt",
            Scan.status == "accepted",
            Scan.product_ean.isnot(None),
            Scan.receipt_id.isnot(None),
        )
        .group_by(Scan.product_ean)
    )
    rows = db.execute(stmt).all()

    suggestions = []
    for row in rows:
        freq = row.appearances / total
        if freq >= threshold:
            product = db.get(Product, row.product_ean)
            name = product.name if product else row.product_ean
            suggestions.append(
                SuggestedProduct(
                    product_ean=row.product_ean,
                    product_name=name,
                    frequency=round(freq, 2),
                    appearances=row.appearances,
                    total_receipts=total,
                )
            )

    suggestions.sort(key=lambda s: s.frequency, reverse=True)
    return suggestions


def generate_and_add_to_list(
    db: Session,
    user_id: uuid.UUID,
    list_id: uuid.UUID,
) -> tuple[list[SuggestedProduct], int]:
    """Generate suggestions and add new ones to the shopping list."""
    sl = db.get(ShoppingList, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    suggestions = suggest_products(db, user_id)

    # Route every add through shopping_list_service so suggestions respect
    # the max_items_per_list cap and the ListFull guard (LO-24). Once the
    # list is full, stop — further suggestions cannot be added.
    added = 0
    for s in suggestions:
        try:
            list_svc.add_item_to_list(db, list_id, user_id, s.product_ean, quantity=1)
            added += 1
        except list_svc.ItemAlreadyInList:
            continue
        except list_svc.ListFull:
            break

    return suggestions, added
