from __future__ import annotations

import uuid

from ratis_core.exceptions import NotFound
from ratis_core.schemas import ProductDetailResponse
from repositories.barcode_repository import (
    get_local_price,
    get_nearby_prices,
    get_product,
    get_search_radius,
)
from sqlalchemy.orm import Session


def get_product_detail(
    db: Session,
    *,
    ean: str,
    user_id: uuid.UUID,
    store_id: uuid.UUID | None,
    user_lat: float | None,
    user_lng: float | None,
) -> dict:
    product = get_product(db, ean)
    if product is None:
        raise NotFound("product_not_found")

    # ``price_consensus.price`` is an integer number of cents (int-cents,
    # per CLAUDE.md). The wire field is named ``price_cents`` so the unit is
    # unambiguous — the client consumes it directly with no float / no
    # ``* 100`` conversion.
    local_price = None
    if store_id:
        consensus = get_local_price(db, store_id, ean)
        if consensus:
            local_price = {
                "store_id": str(consensus.store_id),
                "price_cents": consensus.price,
                "last_seen_at": consensus.last_seen_at.isoformat(),
            }

    nearby_prices = []
    if user_lat is not None and user_lng is not None:
        radius_km = get_search_radius(db, user_id)
        rows = get_nearby_prices(
            db,
            ean=ean,
            lat=user_lat,
            lng=user_lng,
            radius_km=radius_km,
            exclude_store_id=store_id,
        )
        nearby_prices = [
            {
                "store_id": str(r["store_id"]),
                "store_name": r["store_name"],
                "price_cents": r["price"],
                "distance_km": round(float(r["distance_km"]), 2),
            }
            for r in rows
        ]

    return {
        "product": ProductDetailResponse.model_validate(product).model_dump(),
        "local_price": local_price,
        "nearby_prices": nearby_prices,
    }
