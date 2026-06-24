"""Per-brand gift-card aggregate stats for the shop screen.

The mobile shop UI used to walk every entry of ``GET /rewards/gift-cards``
to compute the per-brand orders count + total saved. That breaks the
moment we paginate the gift-cards list (which is a V1.x todo) and wastes
bandwidth (full payload for an aggregate). This service ships the
aggregate as a single SQL ``COUNT/SUM/MIN/MAX`` instead.

Cf F-13 in the V1.1 usage-stats sprint.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy import text
from sqlalchemy.orm import Session


class ShopUsageStats(TypedDict):
    brand_id: str
    orders_count: int
    total_saved_cents: int
    first_order_at: str | None
    last_order_at: str | None


def get_brand_usage_stats(db: Session, *, user_id: Any, brand_id: uuid.UUID) -> ShopUsageStats:
    """Aggregate the user's delivered orders for a single brand.

    Undelivered terminal statuses are excluded — the user never received the
    gift card, so it would be misleading to surface them as "savings":
    - ``'failed'``  : real Runa issuance failure.
    - ``'churned'`` : churn-farming cancellation (added in migration
      ``20260517_1600_gift_card_churned_status``; previously written as
      ``'failed'``, now has its own distinct status — H3 audit fix).

    Pending + issued orders both count : a pending order represents committed
    CAB (it cannot be cancelled by the user) and will be issued or refunded
    server-side ; either way the user has earned the value.

    The aggregate covers **all** ``source_type``s (shop_purchase /
    annual_subscription / battlepass_milestone / referral_reward) — the
    surface is "what gift-cards did I get from this brand", which is
    source-agnostic.

    Returns the all-zero / nulls shape when no order matches — the
    response shape stays stable so the mobile client doesn't branch.
    """
    row = db.execute(
        text(
            "SELECT "
            "  COUNT(*)               AS orders_count, "
            "  COALESCE(SUM(denomination), 0) AS total_saved_cents, "
            "  MIN(created_at)        AS first_order_at, "
            "  MAX(created_at)        AS last_order_at "
            "FROM gift_card_orders "
            "WHERE user_id = :uid "
            "  AND brand_id = :bid "
            "  AND status NOT IN ('failed', 'churned')"
        ),
        {"uid": user_id, "bid": brand_id},
    ).first()

    return ShopUsageStats(
        brand_id=str(brand_id),
        orders_count=int(row.orders_count) if row else 0,
        total_saved_cents=int(row.total_saved_cents) if row else 0,
        first_order_at=row.first_order_at.isoformat() if row and row.first_order_at else None,
        last_order_at=row.last_order_at.isoformat() if row and row.last_order_at else None,
    )
