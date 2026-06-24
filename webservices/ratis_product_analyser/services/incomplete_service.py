"""« Compléter ce produit » — backend service.

Surfaces products with at least one missing field (brands_text,
categories_tags, labels_tags) ranked by cross-user popularity
(COUNT of matched scans + list_items, deduped per source). The
front-end consumes the result as a batch of EnrichissementTask
shapes — see
``docs/superpowers/specs/2026-05-14-completer-screen-design.md``.

`name` is NOT in the missing-field surface because
``products.name`` is `NOT NULL` with CHECK `name <> ''` (see
`ratis_core/ratis_core/models/product.py`). The /contribute
route still accepts `name` as a Pydantic Literal value for typo-
fix flows, but the /incomplete endpoint never surfaces a
name-completion task.
"""

from __future__ import annotations

from typing import Any

from ratis_core.settings import load_settings as _load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

# Re-export under module-local name so tests can patch
# ``services.incomplete_service.load_settings`` tightly without touching
# the upstream ratis_core.settings module that other services depend on.
load_settings = _load_settings


_INCOMPLETE_SQL = text(
    """
    WITH popularity AS (
      SELECT product_ean, COUNT(*) AS pop
      FROM (
        SELECT product_ean FROM scans
        WHERE status = 'matched' AND product_ean IS NOT NULL
        UNION ALL
        SELECT product_ean FROM shopping_list_items
      ) AS combined
      GROUP BY product_ean
    )
    SELECT
      p.ean,
      p.name,
      p.brands_text,
      p.categories_tags,
      p.labels_tags,
      COALESCE(pop.pop, 0) AS popularity_score
    FROM products p
    LEFT JOIN popularity pop ON p.ean = pop.product_ean
    WHERE
      p.source <> 'user_suggested'
      AND (
        (p.brands_text IS NULL OR p.brands_text = '')
        OR (p.categories_tags IS NULL OR cardinality(p.categories_tags) = 0)
        OR (p.labels_tags IS NULL OR cardinality(p.labels_tags) = 0)
      )
    ORDER BY popularity_score DESC, random()
    LIMIT :limit
    """
)


def _query_popular_incomplete_rows(db: Session, limit: int) -> list:
    """Return rows of incomplete products ranked by popularity.

    Each row exposes ``.ean``, ``.name``, ``.brands_text``,
    ``.categories_tags``, ``.labels_tags``, ``.popularity_score``. Caller
    is responsible for :func:`_pick_missing_field` + reward attachment.
    """
    return db.execute(_INCOMPLETE_SQL, {"limit": limit}).all()


def _pick_missing_field(row) -> str | None:
    """Priority order : brands > categories_tags > labels_tags.

    ``name`` is NOT in the priority chain because ``products.name`` is
    ``NOT NULL`` with CHECK ``name <> ''`` in the schema, so it can
    never be a missing-field target. The ``/contribute`` route still
    accepts ``name`` as a Pydantic Literal value for typo-fix flows.
    """
    if not row.brands_text:
        return "brands"
    if not row.categories_tags or len(row.categories_tags) == 0:
        return "categories_tags"
    if not row.labels_tags or len(row.labels_tags) == 0:
        return "labels_tags"
    return None


def list_incomplete_products(
    db: Session,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Compose the « Compléter ce produit » batch.

    Returns a list of EnrichissementTask-shaped dicts ready to ship
    over HTTP. Each task carries the SAME ``cab_reward`` value
    (read from ratis_settings — uniform per-field reward).

    ``cab_per_fill_product_field`` lives under the ``rewards`` section
    of the settings tree (cf
    ``ratis_core/ratis_core/config/ratis_settings.json``).
    """
    rows = _query_popular_incomplete_rows(db, limit=limit)
    cab_reward = load_settings()["rewards"]["cab_per_fill_product_field"]
    out: list[dict[str, Any]] = []
    for row in rows:
        field = _pick_missing_field(row)
        if field is None:
            # Safety net : the SQL WHERE should never return a fully
            # complete row, but if invariants ever break we drop it
            # rather than emit a nonsense task with missing_field=None.
            continue
        out.append(
            {
                "product_ean": row.ean,
                "product_name": row.name,
                "missing_field": field,
                "cab_reward": cab_reward,
            }
        )
    return out
