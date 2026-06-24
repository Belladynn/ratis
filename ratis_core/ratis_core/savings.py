"""
Total savings computation shared by ratis_auth (live delta + stats route) and
ratis_batch_savings (nightly snapshot recompute).

Formula (per accepted `receipt` scan of user U) :
    savings = max(0, max_consensus_price - scan.price) * scan.quantity

Where `max_consensus_price` is :
    1) MAX(price_consensus.price) over nearby stores within the user's
       `search_radius_km` around `users.ref_lat/ref_lng`, matching `product_ean`.
    2) Fallback : MAX(price_consensus.price) anywhere (no radius filter).
    3) Fallback 2 : NULL → contribution is 0 for that scan.

`total_savings_cents = Σ scan_savings` (lifetime, all time).

All monetary values are integer cents (CLAUDE.md / DA-02).

Design notes :
- One SQL query with CTEs — no per-scan round-trip.
- Radius filtering uses PostGIS `ST_DWithin` on `stores.geog` (GIST-indexed).
- Returns 0 when `users.ref_lat IS NULL` : caller is responsible for surfacing
  the `location_missing` flag. The fallback-to-global rule only applies AFTER
  the user has a location, because a user with no location shouldn't see
  phantom savings computed from a random store on the other side of the world.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_SAVINGS_SQL = text("""
WITH user_info AS (
    SELECT
        u.ref_lat,
        u.ref_lng,
        COALESCE(up.search_radius_km, 5) AS radius
    FROM users u
    LEFT JOIN user_preferences up ON up.user_id = u.id
    WHERE u.id = :uid
),
nearby_stores AS (
    SELECT s.id
    FROM stores s, user_info ui
    WHERE ui.ref_lat IS NOT NULL
      AND NOT s.is_disabled
      AND s.geog IS NOT NULL
      AND ST_DWithin(
            s.geog,
            ST_SetSRID(
                ST_MakePoint(
                    ui.ref_lng::double precision,
                    ui.ref_lat::double precision
                ),
                4326
            )::geography,
            ui.radius * 1000
          )
),
scan_savings AS (
    SELECT
        GREATEST(
            0,
            COALESCE(
                (
                    SELECT MAX(pc.price)
                    FROM price_consensus pc
                    WHERE pc.product_ean = sc.product_ean
                      AND pc.store_id IN (SELECT id FROM nearby_stores)
                ),
                (
                    SELECT MAX(pc.price)
                    FROM price_consensus pc
                    WHERE pc.product_ean = sc.product_ean
                )
            ) - sc.price
        ) * sc.quantity AS savings
    FROM scans sc
    WHERE sc.user_id = :uid
      AND sc.status = 'accepted'
      AND sc.scan_type = 'receipt'
      AND sc.product_ean IS NOT NULL
      AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR sc.scanned_at >= CAST(:since AS TIMESTAMPTZ))
)
SELECT COALESCE(SUM(savings), 0)::BIGINT AS total FROM scan_savings
""")


def compute_savings_for_user(
    db: Session,
    user_id: uuid.UUID | str,
    since: datetime | None = None,
) -> int:
    """
    Compute total savings (cents) for a user's accepted receipt-type scans.

    If ``since`` is provided, only scans with ``scanned_at >= since`` are
    considered — used by the /account/stats route to compute the delta since
    the last batch snapshot.

    Returns 0 when the user has no ``ref_lat`` (the caller should set the
    ``location_missing`` flag).
    """
    # Short-circuit : user with no location gets 0. Prevents leaking a random
    # global-fallback max-consensus number before the user opts in.
    row = (
        db.execute(
            text("SELECT ref_lat FROM users WHERE id = :uid"),
            {"uid": str(user_id)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["ref_lat"] is None:
        return 0

    result = db.execute(
        _SAVINGS_SQL,
        {"uid": str(user_id), "since": since},
    ).scalar_one()
    return int(result or 0)
