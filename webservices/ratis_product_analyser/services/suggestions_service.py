"""Default search suggestions — tier composition for the empty-state of the
Liste/Produit search field. See
``docs/superpowers/specs/2026-05-14-default-search-3tier-design.md``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from ratis_core.models.product import Product
from ratis_core.suggestions_config import load_curated_eans as _load_curated_eans
from sqlalchemy import select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Re-export under a module-local name so tests can patch
# ``services.suggestions_service.load_curated_eans`` without reaching into
# ratis_core. Keeps the patch boundary tight.
load_curated_eans = _load_curated_eans


def _query_user_recent_eans(
    db: Session,
    user_id: UUID,
    limit: int,
) -> list[str]:
    """Return up to ``limit`` distinct product EANs from the user's history,
    sorted by most-recent activity (UNION of matched scans + shopping list
    items, deduped by EAN, MAX(timestamp) as recency).

    Uses a single SQL UNION ALL inside a subquery so the database does the
    dedupe + sort in one pass. Existing indexes on ``scans(user_id)`` and
    ``shopping_list_items(list_id)`` (via the FK + the unique constraint
    on ``shopping_lists.user_id``) make this sub-50ms on realistic data.
    """
    sql = text(
        """
        SELECT product_ean, MAX(occurred_at) AS recency_ts
        FROM (
            SELECT product_ean, scanned_at AS occurred_at
            FROM scans
            WHERE user_id = :uid
              AND status = 'matched'
              AND product_ean IS NOT NULL
            UNION ALL
            SELECT sli.product_ean, sli.created_at AS occurred_at
            FROM shopping_list_items sli
            JOIN shopping_lists sl ON sli.list_id = sl.id
            WHERE sl.user_id = :uid
        ) AS combined
        GROUP BY product_ean
        ORDER BY recency_ts DESC
        LIMIT :limit
        """
    )
    rows = db.execute(sql, {"uid": str(user_id), "limit": limit}).all()
    return [row.product_ean for row in rows]


def _hydrate_with_products(
    db: Session,
    eans: list[str],
) -> list[dict[str, Any]]:
    """Bulk-fetch ``products`` rows for the given EANs, preserving input
    order. Missing EANs are silently skipped with a WARN log (catalogue
    rotation does not break boot — see KP entry on curated config drift).

    Returns a list of ``ProductSearchHit``-compatible dicts so the route
    layer can reuse the existing ``ProductSearchHit`` Pydantic model
    without any extra mapping step.
    """
    if not eans:
        return []
    stmt = select(Product).where(Product.ean.in_(eans))
    rows = db.execute(stmt).scalars().all()
    by_ean = {p.ean: p for p in rows}
    missing = [e for e in eans if e not in by_ean]
    if missing:
        logger.warning(
            "default_suggestions hydration: %d EAN(s) missing from products: %s",
            len(missing),
            missing,
        )
    out: list[dict[str, Any]] = []
    for ean in eans:
        p = by_ean.get(ean)
        if p is None:
            continue
        out.append(
            {
                "ean": p.ean,
                "name": p.name,
                "brands": getattr(p, "brands_text", None),
                "quantity": getattr(p, "quantity_text", None),
                "categories_tags": getattr(p, "categories_tags", None),
                "labels_tags": getattr(p, "labels_tags", None),
                "origins_tags": getattr(p, "origins_tags", None),
                "source": p.source,
            }
        )
    return out


def get_default_suggestions(
    db: Session,
    user_id: UUID,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Compose tier (c) [user history] topped up with tier (b) [curated
    French staples] to always emit ``limit`` rows when possible.

    Algorithm :
        1. Pull up to ``limit`` user EANs (recency-sorted, deduped)
        2. If we got ``>= limit``, hydrate + return.
        3. Else, top up with curated EANs not already in the user set,
           preserving the curated config order. Hydrate the union.
    """
    user_eans = _query_user_recent_eans(db, user_id, limit)
    if len(user_eans) >= limit:
        return _hydrate_with_products(db, user_eans[:limit])

    curated = load_curated_eans()
    seen = set(user_eans)
    filler = [e for e in curated if e not in seen][: limit - len(user_eans)]
    return _hydrate_with_products(db, user_eans + filler)
