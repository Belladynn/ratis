"""Price lookups: local consensus + national average fallback."""

from __future__ import annotations

import uuid
from decimal import Decimal

from ratis_core.models.price import PriceConsensus
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def get_local_price(db: Session, store_id: uuid.UUID, product_ean: str) -> tuple[int, Decimal] | None:
    """Return (price_cents, trust_score) from local consensus, or None."""
    stmt = select(PriceConsensus.price, PriceConsensus.trust_score).where(
        PriceConsensus.store_id == store_id,
        PriceConsensus.product_ean == product_ean,
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    return row.price, row.trust_score


def get_national_average(db: Session, product_ean: str) -> tuple[Decimal, int] | None:
    """Return (avg_price_cents, count) across all stores, or None if no data."""
    stmt = select(
        func.avg(PriceConsensus.price).label("avg_price"),
        func.count(PriceConsensus.id).label("cnt"),
    ).where(PriceConsensus.product_ean == product_ean)
    row = db.execute(stmt).first()
    if row is None or row.cnt == 0:
        return None
    return row.avg_price, row.cnt
