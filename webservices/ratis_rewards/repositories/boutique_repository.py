"""Boutique V1 repository — raw SQL queries for the gift-card shop.

Lives next to ``gift_card_repository`` because it shares the
``gift_card_orders`` table for the boutique flow (``source_type =
'shop_purchase'``). Kept in its own module to avoid bloating the parent
repository (the boutique caps and idempotency logic are specific to
user-initiated purchases — annual / battlepass / referral orders bypass
all four caps).

All amounts are INTEGER centimes.

Daily / weekly cap calculations use ``date_trunc(... AT TIME ZONE
'Europe/Paris')`` so the cap rolls over at Paris midnight (R-cap-tz).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.gift_card_repository import (
    insert_gift_card_order,
    sum_redeemed_window,
)

# Source-type emitted by the boutique flow. Distinct from the legacy
# 'annual_subscription', 'battlepass_milestone' and 'referral_reward'
# values which bypass these caps.
SHOP_PURCHASE_SOURCE_TYPE = "shop_purchase"


def get_active_brands(db: Session) -> list[dict[str, Any]]:
    """Return active gift-card brands ordered by name."""
    rows = db.execute(
        text(
            "SELECT id, name, logo_url, provider_brand_id "
            "FROM gift_card_brands WHERE is_active = TRUE "
            "ORDER BY name ASC"
        )
    ).fetchall()
    return [
        {
            "id": r.id,
            "name": r.name,
            "logo_url": r.logo_url,
            "provider_brand_id": r.provider_brand_id,
        }
        for r in rows
    ]


def get_brand_if_active(db: Session, brand_id: uuid.UUID) -> dict[str, Any] | None:
    """Return brand info if the row exists AND is_active=true. None otherwise."""
    row = db.execute(
        text("SELECT id, name, logo_url, provider_brand_id, is_active FROM gift_card_brands WHERE id = :bid"),
        {"bid": brand_id},
    ).first()
    if row is None or not row.is_active:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "logo_url": row.logo_url,
        "provider_brand_id": row.provider_brand_id,
    }


def count_redeemed_today_cents(db: Session, user_id: uuid.UUID) -> int:
    """Sum of denominations bought today (Paris-local) via the boutique.

    ``exclude_failed=True`` — a failed order (Runa 5xx, network error,
    annual-cap BLOCK) delivered nothing and must not consume the cap.
    """
    return sum_redeemed_window(
        db,
        user_id,
        source_type=SHOP_PURCHASE_SOURCE_TYPE,
        window="day",
        exclude_failed=True,
    )


def count_redeemed_this_week_cents(db: Session, user_id: uuid.UUID) -> int:
    """Sum of denominations bought this ISO week (Paris-local).

    ``exclude_failed=True`` — see :func:`count_redeemed_today_cents`.
    """
    return sum_redeemed_window(
        db,
        user_id,
        source_type=SHOP_PURCHASE_SOURCE_TYPE,
        window="week",
        exclude_failed=True,
    )


def find_recent_duplicate_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    brand_id: uuid.UUID,
    denomination_cents: int,
    window_seconds: int,
) -> uuid.UUID | None:
    """Return the order id of an exact-match boutique order within the last
    ``window_seconds`` seconds (anti-double-tap). None if no match.
    """
    row = db.execute(
        text(
            "SELECT id FROM gift_card_orders "
            "WHERE user_id = :uid "
            "  AND brand_id = :bid "
            "  AND denomination = :denom "
            "  AND source_type = :stype "
            "  AND created_at >= NOW() - make_interval(secs => :win) "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination_cents,
            "stype": SHOP_PURCHASE_SOURCE_TYPE,
            "win": window_seconds,
        },
    ).first()
    return row.id if row else None


def insert_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    brand_id: uuid.UUID,
    denomination_cents: int,
    source_ref_id: str,
) -> uuid.UUID:
    """Insert a new shop_purchase order (status='pending') and return its id.

    Thin boutique-flavoured wrapper over
    :func:`gift_card_repository.insert_gift_card_order` — pins
    ``source_type='shop_purchase'``. Idempotent via the existing
    UNIQUE(source_type, source_ref_id) ; the boutique flow uses the
    cabecoin_transactions.id (UUID4) as source_ref_id, so replays are
    essentially impossible in practice.
    """
    return insert_gift_card_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination_cents=denomination_cents,
        source_type=SHOP_PURCHASE_SOURCE_TYPE,
        source_ref_id=source_ref_id,
    )


def get_user_ytd_cents(db: Session, user_id: uuid.UUID) -> int:
    """Return the current users.gift_card_redeemed_ytd_cents (0 if missing)."""
    row = db.execute(
        text("SELECT gift_card_redeemed_ytd_cents FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).first()
    return int(row.gift_card_redeemed_ytd_cents) if row else 0
