from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ratis_core.models.analytics import UserPreferences
from ratis_core.models.price import PriceConsensus
from ratis_core.models.product import Product
from ratis_core.models.scan import Scan
from ratis_core.products import claim_first_discovery
from sqlalchemy import select, text
from sqlalchemy.orm import Session

_DEFAULT_RADIUS_KM = 5

# Two pre-compiled query variants avoid f-string SQL injection patterns.
_NEARBY_SQL = text("""
    WITH distances AS (
        SELECT
            s.id          AS store_id,
            s.name        AS store_name,
            pc.price,
            pc.last_seen_at,
            ST_Distance(
                s.geog, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
            ) / 1000.0 AS distance_km
        FROM stores s
        JOIN price_consensus pc ON pc.store_id = s.id
        WHERE pc.product_ean = :ean
          AND s.is_disabled = false
          AND s.geog IS NOT NULL
          AND ST_DWithin(
              s.geog,
              ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
              :radius_km * 1000
          )
    )
    SELECT * FROM distances ORDER BY price ASC
""")

_NEARBY_EXCLUDE_SQL = text("""
    WITH distances AS (
        SELECT
            s.id          AS store_id,
            s.name        AS store_name,
            pc.price,
            pc.last_seen_at,
            ST_Distance(
                s.geog, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
            ) / 1000.0 AS distance_km
        FROM stores s
        JOIN price_consensus pc ON pc.store_id = s.id
        WHERE pc.product_ean = :ean
          AND s.is_disabled = false
          AND s.id != CAST(:exclude_id AS uuid)
          AND s.geog IS NOT NULL
          AND ST_DWithin(
              s.geog,
              ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
              :radius_km * 1000
          )
    )
    SELECT * FROM distances ORDER BY price ASC
""")


def get_product(db: Session, ean: str) -> Product | None:
    return db.get(Product, ean)


def get_local_price(db: Session, store_id: uuid.UUID, ean: str) -> PriceConsensus | None:
    return db.scalar(
        select(PriceConsensus).where(
            PriceConsensus.store_id == store_id,
            PriceConsensus.product_ean == ean,
        )
    )


def get_nearby_prices(
    db: Session,
    ean: str,
    lat: float,
    lng: float,
    radius_km: int,
    exclude_store_id: uuid.UUID | None = None,
) -> list[dict]:
    """
    Return stores (with price_consensus for ean) within radius_km of (lat, lng),
    sorted by price ascending.  Filtrage par rayon via PostGIS `ST_DWithin`
    (index GIST sur `stores.geog`).
    """
    if exclude_store_id:
        rows = (
            db.execute(
                _NEARBY_EXCLUDE_SQL,
                {"ean": ean, "lat": lat, "lng": lng, "radius_km": radius_km, "exclude_id": str(exclude_store_id)},
            )
            .mappings()
            .all()
        )
    else:
        rows = (
            db.execute(
                _NEARBY_SQL,
                {"ean": ean, "lat": lat, "lng": lng, "radius_km": radius_km},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def get_search_radius(db: Session, user_id: uuid.UUID) -> int:
    prefs = db.get(UserPreferences, user_id)
    return prefs.search_radius_km if prefs else _DEFAULT_RADIUS_KM


def get_scan(db: Session, scan_id: uuid.UUID) -> Scan | None:
    return db.get(Scan, scan_id)


def resolve_scan(db: Session, scan: Scan, product_ean: str, match_method: str | None = None) -> None:
    scan.product_ean = product_ean
    scan.status = "accepted"
    scan.match_method = match_method
    scan.user_verified_at = datetime.now(UTC)
    db.flush()
    # V1.1 first-discovery attribution (KP-75) — barcode rescue path.
    # Helper is idempotent + filters banned/deleted users itself.
    if scan.user_id:
        claim_first_discovery(db, product_ean, scan.user_id)
