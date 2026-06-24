"""
GET  /api/v1/gamification/streak                 — current streak state
POST /api/v1/gamification/streak/feed            — feed Jack (daily action)
POST /api/v1/gamification/streak/repair          — repair streak (gap=1, no reserves)
POST /api/v1/gamification/streak/purchase-reserve — buy food reserves with CABs
"""

from __future__ import annotations

import logging

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.models.user import User
from repositories.cab_repository import InsufficientBalance
from repositories.challenge_repository import maybe_increment_challenge
from repositories.streak_repository import (
    ReserveLimitExceeded,
    StreakNeedsRepair,
    StreakNotInRepairState,
    feed_jack,
    get_streak,
    purchase_reserve,
    repair_streak,
)
from repositories.xp_repository import award_xp
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class FeedJackRequest(BaseModel):
    timezone: str | None = Field(
        default=None,
        description="IANA timezone string (e.g. 'Europe/Paris'). Send on first call or when device timezone changes.",
    )


class PurchaseReserveRequest(BaseModel):
    quantity: int = Field(ge=1, description="Number of food reserves to purchase (min 1)")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/gamification/streak")
def get_streak_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the current Feed Jack streak state for the authenticated user."""
    return get_streak(db, current_user.id)


@router.post("/gamification/streak/feed")
def feed_jack_endpoint(
    body: FeedJackRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Feed Jack.

    - Advances the streak by 1 (consecutive days).
    - Auto-consumes food reserves when days were missed.
    - Returns 409 needs_repair_required when gap=1 and no reserves (use /repair instead).
    - Awards XP for the feed action (multiplied by active streak).
    - Idempotent: feeding twice in the same day returns the current state.
    """
    cfg = request.app.state.cfg
    xp_per_feed: int = cfg["xp"]["xp_per_feed_jack"]

    try:
        with db_transaction(db):
            result = feed_jack(db, current_user.id, xp_per_feed=xp_per_feed, tz_hint=body.timezone)
            if result.is_new_feed:
                # Award XP — multiplier is applied automatically inside award_xp.
                # The streak row will be committed together with the XP transaction.
                award_xp(db, current_user.id, xp_per_feed, "feed_jack")
                maybe_increment_challenge(db, current_user.id, "feed_jack")

                # Achievements V1 — fire-and-forget. The streak just extended
                # (is_new_feed=True), so trigger the `streak_extended` event
                # which evaluates the `streak_days` trigger handler against
                # the freshly-updated user_streaks.current_streak_days.
                # No standalone streak_service exists — feed_jack is the
                # extension primitive and the route is its only orchestrator.
                try:
                    from services import achievement_service

                    achievement_service.check_achievements(
                        db,
                        user_id=current_user.id,
                        event_type="streak_extended",
                        payload={},
                    )
                except Exception:
                    logger.exception(
                        "achievement_hook_streak_extended_failed",
                        extra={"user_id": str(current_user.id)},
                    )
    except StreakNeedsRepair:
        raise HTTPException(status_code=409, detail="needs_repair_required")
    return result.state


@router.post("/gamification/streak/repair")
def repair_streak_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Repair a broken streak.

    Only available when gap_days==1 and food_reserves==0.
    Costs food_reserve_cost_cab CABs. Returns 409 if not in repair state, 402 if
    insufficient CABs.
    """
    cfg = request.app.state.cfg
    repair_cost: int = cfg["gamification"]["feed_jack"]["food_reserve_cost_cab"]

    try:
        with db_transaction(db):
            state = repair_streak(db, current_user.id, repair_cost_cab=repair_cost)
    except StreakNotInRepairState:
        raise HTTPException(status_code=409, detail="streak_not_in_repair_state")
    except InsufficientBalance:
        raise HTTPException(status_code=402, detail="insufficient_cab_balance")
    return state


@router.post("/gamification/streak/purchase-reserve")
def purchase_reserve_endpoint(
    body: PurchaseReserveRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Purchase food reserves for Jack using CABs.

    Returns 409 if quantity would exceed max_food_reserves.
    Returns 402 if insufficient CAB balance.
    """
    cfg = request.app.state.cfg
    fj_cfg = cfg["gamification"]["feed_jack"]
    cost_per_reserve: int = fj_cfg["food_reserve_cost_cab"]
    max_reserves: int = fj_cfg["max_food_reserves"]

    try:
        with db_transaction(db):
            result = purchase_reserve(
                db,
                current_user.id,
                quantity=body.quantity,
                cost_per_reserve_cab=cost_per_reserve,
                max_food_reserves=max_reserves,
            )
    except ReserveLimitExceeded:
        raise HTTPException(status_code=409, detail="reserve_limit_exceeded")
    except InsufficientBalance:
        raise HTTPException(status_code=402, detail="insufficient_cab_balance")
    return result
