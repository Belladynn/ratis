"""GET /rewards/shop/{brand_id}/usage-stats — per-brand aggregate.

Replaces the client-side reducer in
``ratis_client/app/shop/[brand_id].tsx`` that walked the entire
``GET /rewards/gift-cards`` payload to compute the per-brand orders count
+ total saved. See :mod:`services.shop_usage_stats_service` for the
aggregate semantics.

Cf F-13 in the V1.1 usage-stats sprint.
"""

from __future__ import annotations

import uuid

from deps import get_current_user
from fastapi import APIRouter, Depends
from ratis_core.database import get_db
from services.shop_usage_stats_service import get_brand_usage_stats
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/rewards/shop/{brand_id}/usage-stats")
def read_shop_usage_stats(
    brand_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """Return the user's aggregate stats for the given gift-card brand.

    Always returns a 200 with the canonical shape (no 404 when the user
    has no orders for the brand) so the client can render an "empty
    state" without an extra error branch.
    """
    return dict(get_brand_usage_stats(db, user_id=current_user.id, brand_id=brand_id))
