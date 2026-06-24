"""
Create or find a Store from an already-geocoded address.

Part B of scan reconciliation — when a receipt is uploaded for a shop we
don't yet have in ``stores``, the caller geocodes outside of this module
and hands us the resulting coordinates. We never use the user's scan
coords (PII stays out of the stores table).

Since DA-35 the runtime path no longer produces those coordinates: the
local ``store_matching_service`` identifies the store by retailer_id +
postal_code directly, so this helper is currently only used by legacy /
admin paths that still manage a manual coordinate input.

A dedup pass prevents creating a near-duplicate of an existing store:
same retailer + distance PostGIS `ST_DWithin` <= 50m → return the existing row.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from ratis_core.models.store import Store
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DEDUP_RADIUS_KM = 0.05  # 50m


_DEDUP_SQL = text("""
    SELECT id
    FROM stores
    WHERE is_disabled = false
      AND retailer IS NOT NULL
      AND lower(retailer) = lower(:retailer)
      AND geog IS NOT NULL
      AND ST_DWithin(
          geog,
          ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
          :radius_km * 1000
      )
    ORDER BY geog <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
    LIMIT 1
""")


def _find_existing_nearby(db: Session, retailer: str, lat: float, lng: float) -> uuid.UUID | None:
    row = db.execute(
        _DEDUP_SQL,
        {"retailer": retailer, "lat": lat, "lng": lng, "radius_km": DEDUP_RADIUS_KM},
    ).first()
    return row[0] if row else None


def create_store_from_receipt(
    db: Session,
    *,
    retailer: str,
    address_raw: str,
    coords: tuple[float, float],
) -> Store:
    """
    Return an existing same-retailer store within DEDUP_RADIUS_KM of coords,
    else INSERT a new one using the Nominatim coords (never user geo).

    The caller is responsible for commit — this function only flushes so the
    returned Store has a populated id.
    """
    lat, lng = coords

    existing_id = _find_existing_nearby(db, retailer=retailer, lat=lat, lng=lng)
    if existing_id is not None:
        existing = db.get(Store, existing_id)
        if existing is not None:
            log.info(
                "Store dedup hit for retailer=%s near (%.4f,%.4f) → %s",
                retailer,
                lat,
                lng,
                existing.id,
            )
            return existing

    store = Store(
        name=retailer.title(),
        retailer=retailer,
        address=address_raw,
        lat=Decimal(str(lat)),
        lng=Decimal(str(lng)),
        is_disabled=False,
        source="user_suggested",
    )
    db.add(store)
    db.flush()
    log.info(
        "New store created from receipt retailer=%s id=%s",
        retailer,
        store.id,
    )
    return store
