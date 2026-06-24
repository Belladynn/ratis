"""Price resolution service — local consensus -> national average -> unknown."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from ratis_core.settings import load_settings
from repositories import price_repository as repo
from sqlalchemy.orm import Session


@dataclass
class PriceResult:
    product_ean: str
    store_id: uuid.UUID
    price: float | None
    price_source: str  # "consensus_local" | "national_average" | "unknown"
    trust_score: float | None
    warning: str | None = None


def resolve_price(db: Session, product_ean: str, store_id: uuid.UUID) -> PriceResult:
    """Resolve price with 3-tier fallback."""
    # 1) Local consensus
    local = repo.get_local_price(db, store_id, product_ean)
    if local is not None:
        price_cents, trust = local
        # Convert cents -> euros via Decimal, never float division (rounding drift).
        return PriceResult(
            product_ean=product_ean,
            store_id=store_id,
            price=float(Decimal(price_cents) / 100),
            price_source="consensus_local",
            trust_score=float(round(trust, 2)) if trust is not None else None,
        )

    # 2) National average
    cfg = load_settings().get("list_optimiser", {})
    min_dp = cfg.get("national_avg_min_datapoints", 5)

    national = repo.get_national_average(db, product_ean)
    if national is not None:
        avg_price_cents, count = national
        if count >= min_dp:
            return PriceResult(
                product_ean=product_ean,
                store_id=store_id,
                price=float(round(Decimal(avg_price_cents) / 100, 2)),
                price_source="national_average",
                trust_score=None,
                warning="price_not_reliable",
            )

    # 3) Unknown
    return PriceResult(
        product_ean=product_ean,
        store_id=store_id,
        price=None,
        price_source="unknown",
        trust_score=None,
        warning="price_unknown_insufficient_data",
    )
