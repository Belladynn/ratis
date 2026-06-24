"""Business logic for product text search (wave 4 Bug 3).

Thin layer on top of ``repositories.product_search_repository`` — the
route owns the Pydantic shape and we keep validation in the route via
FastAPI ``Query(...)`` constraints. This module exists to enforce the
``routes → services → repositories`` layering (SA_DEV R03) and as the
natural extension point for future ranking tweaks (e.g. pg_trgm
similarity-score weighting).
"""

from __future__ import annotations

from repositories.product_search_repository import search_products
from sqlalchemy.orm import Session


def run_product_search(db: Session, *, query: str, limit: int) -> dict:
    items = search_products(db, query=query, limit=limit)
    return {"items": items}
