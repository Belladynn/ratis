"""Server-authoritative cap usage projection for gift-card redemptions.

Computes, for a given user :
  - ``ytd_cents`` from ``users.gift_card_redeemed_ytd_cents`` (denorm).
  - ``daily_cents`` / ``weekly_cents`` via SUM over ``gift_card_orders``
    filtered by ``source_type='shop_purchase'`` and excluding ``failed``,
    using the same Europe/Paris cutoff as the boutique caps
    (cf ``repositories/boutique_repository.py``).
  - The thresholds + caps from ``ratis_settings.json`` (boutique +
    gift_cards sections).

The endpoint :func:`routes.rewards.gift_cards.read_cap_usage` is the
single read surface — the mobile client must not aggregate orders
client-side anymore (cf F-11 in the V1.1 usage-stats sprint). Replaces
the legacy ``computeUsageStats`` in ``hooks/use-gift-cards.ts``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from ratis_core.settings import load_settings
from repositories.gift_card_repository import sum_redeemed_window
from sqlalchemy import text
from sqlalchemy.orm import Session


class CapUsage(TypedDict):
    """Shape returned to the client (matches the JSON response 1:1)."""

    year: int
    ytd_cents: int
    annual_warning_threshold_cents: int
    annual_hard_cap_cents: int
    remaining_cents: int
    warning_threshold_reached: bool
    daily_cents: int
    weekly_cents: int
    daily_cap_cents: int
    weekly_cap_cents: int


def _read_caps() -> dict[str, int]:
    """Read all cap-related settings in one shot.

    Defaults are aligned with the seed values in ``ratis_settings.json``
    so the function is robust to a partial seed in alpha environments
    (the route never crashes — it surfaces best-known caps instead).
    """
    cfg = load_settings()
    boutique = cfg.get("boutique", {})
    gift_cards = cfg.get("gift_cards", {})
    return {
        "annual_hard_cap_cents": int(boutique.get("cap_annual_cents", 119900)),
        "daily_cap_cents": int(boutique.get("cap_daily_cents", 10000)),
        "weekly_cap_cents": int(boutique.get("cap_weekly_cents", 30000)),
        "annual_warning_threshold_cents": int(gift_cards.get("annual_warning_threshold_cents", 30500)),
    }


def _get_ytd_cents(db: Session, user_id: Any) -> int:
    row = db.execute(
        text("SELECT gift_card_redeemed_ytd_cents AS v FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).first()
    return int(row.v) if row and row.v is not None else 0


def get_cap_usage(db: Session, user_id: Any) -> CapUsage:
    """Compute the user's full cap-usage snapshot.

    Year is derived from the **server's local clock**. Year-rollover
    (1 Jan 00:00 UTC) is the moment ``ytd_cents`` is reset to 0 by the
    annual reset batch (cf ARCH_cab_economy.md § Reset cap fiscal). No
    timezone subtlety here — the cap is defined per calendar year.
    """
    caps = _read_caps()
    ytd = _get_ytd_cents(db, user_id)
    # Failed orders never consume a cap (a 5xx on the provider should not
    # lock the user out) — hence exclude_failed=True here, unlike the
    # boutique pre-purchase caps which count every non-failed-or-failed row.
    daily = sum_redeemed_window(
        db,
        user_id,
        source_type="shop_purchase",
        window="day",
        exclude_failed=True,
    )
    weekly = sum_redeemed_window(
        db,
        user_id,
        source_type="shop_purchase",
        window="week",
        exclude_failed=True,
    )
    remaining = max(0, caps["annual_hard_cap_cents"] - ytd)
    warning_reached = ytd >= caps["annual_warning_threshold_cents"]
    return CapUsage(
        # UTC year — matches the annual reset cron (cf ARCH_cab_economy.md
        # § Reset cap fiscal : 1 Jan 00:00 UTC).
        year=datetime.now(tz=UTC).year,
        ytd_cents=ytd,
        annual_warning_threshold_cents=caps["annual_warning_threshold_cents"],
        annual_hard_cap_cents=caps["annual_hard_cap_cents"],
        remaining_cents=remaining,
        warning_threshold_reached=warning_reached,
        daily_cents=daily,
        weekly_cents=weekly,
        daily_cap_cents=caps["daily_cap_cents"],
        weekly_cap_cents=caps["weekly_cap_cents"],
    )
