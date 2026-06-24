"""Helper : resolve a ``store_id`` to its parent ``retailer_id``.

Pulled out of ``name_resolution_repository`` so the matcher cascade and
admin service share a single point of truth for the store→retailer
denormalisation lookup. The full rationale (cross-retailer consensus key
swap from ``store_id`` to ``retailer_id``) lives in
``ARCH_cross_retailer_consensus.md`` § "Cascade matcher".
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def resolve_retailer_id(
    db: Session,
    store_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the ``retailer_id`` of the given store, or ``None``.

    Used by the matcher cascade and admin service to convert a
    ``store_id`` into the cross-retailer consensus key. Three outcomes :

    - Store exists with a non-NULL ``retailer_id`` → return it.
    - Store exists but ``retailer_id IS NULL`` (user-suggested store
      pending admin validation, or legacy row that escaped the OSM
      backfill) → return ``None``.
    - Store id does not exist → return ``None`` (defensive : avoids a
      KeyError-style crash on stale UUIDs ; the caller's matcher must
      treat this as "skip consensus, fall through to legacy").

    Read-only — never mutates. The implementation is a single
    ``SELECT retailer_id FROM stores WHERE id = ...`` ; SQLAlchemy's
    session identity map naturally caches the lookup within a single
    request scope. No explicit Redis cache in V1 — store rows are tiny
    and the matcher hits each store at most a few times per scan batch.
    """
    row = db.execute(
        text("SELECT retailer_id FROM stores WHERE id = :store_id"),
        {"store_id": str(store_id)},
    ).first()
    if row is None:
        return None
    retailer_id = row.retailer_id
    if retailer_id is None:
        return None
    if isinstance(retailer_id, uuid.UUID):
        return retailer_id
    return uuid.UUID(str(retailer_id))


__all__ = ["resolve_retailer_id"]
