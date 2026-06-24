"""
CAB service — business logic for Cabecoins balance and scan events.

Phase B (PR #325) extracted the per-event orchestrator
(``handle_scan_accepted``) into ``services.events_service.handle_action``.
This module now only owns the read-side balance composition.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from repositories.cab_repository import (
    get_active_season,
    get_balance,
    get_cab_earned_season,
    get_next_milestone_delta,
)
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_balance_with_battlepass(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """
    Assemble the full GET /rewards/cab/balance response.

    Returns cab_balance + optional battlepass section (None if no active season).
    """
    cab_balance = get_balance(db, user_id)
    season = get_active_season(db)

    if not season:
        return {"cab_balance": cab_balance, "battlepass": None}

    cab_earned_season = get_cab_earned_season(db, user_id, season["id"])
    next_milestone_delta = get_next_milestone_delta(db, user_id, season["id"], cab_earned_season)

    return {
        "cab_balance": cab_balance,
        "battlepass": {
            "season_number": season["season_number"],
            "season_name": season["name"],
            "ends_at": season["ends_at"].isoformat(),
            "cab_earned_season": cab_earned_season,
            "next_milestone_delta": next_milestone_delta,
        },
    }


#
# Referral rewards are now handled by ``services.referral_service`` —
# ``handle_subscription_referral()`` orchestrates CAB + XP + challenge +
# gift card in a single atomic call.
#
# Per-event orchestration (CAB / XP / missions / challenges / battlepass
# / mystery) lives in ``services.events_service.handle_action`` since
# phase B. The legacy ``handle_scan_accepted`` was removed in PR #325 ;
# every caller now POSTs to ``/rewards/events/action`` instead.
#
