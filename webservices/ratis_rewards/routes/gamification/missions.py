"""
GET  /gamification/missions                       — list user missions (lazy gen)
POST /gamification/missions/{id}/claim            — claim mission (multi-claim cumulatif)
POST /gamification/missions/{id}/buffer           — Buffer (= ex-Stonks renommé)
POST /gamification/missions/{id}/burst-claim      — claim Burst paliers (XP)
POST /gamification/missions/{id}/freeze           — freeze mission to next period
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request
from ratis_core.database import get_db
from ratis_core.models.user import User
from repositories.cab_repository import InsufficientBalance
from services.burst_service import claim_burst
from services.missions_service import (
    apply_buffer,
    claim_mission,
    freeze_mission,
    get_missions,
)
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/gamification/missions")
def list_missions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return daily and weekly missions for the authenticated user."""
    today = datetime.now(UTC).date()
    with db_transaction(db):
        result = get_missions(db, current_user.id, today)
    return result


@router.post("/gamification/missions/{user_mission_id}/claim")
def claim_mission_endpoint(
    user_mission_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Claim a mission — multi-claim cumulatif via double gating.

    See ``services/missions_service.claim_mission`` for the full logic.
    """
    with db_transaction(db):
        result = claim_mission(db, current_user.id, user_mission_id)
    return result


@router.post("/gamification/missions/{user_mission_id}/buffer")
def buffer_mission_endpoint(
    user_mission_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Apply 1 Buffer : target × 2, cab_reward × (n+1), period +1 day.

    Buffer is free (no CAB cost). Cap at n=3 daily, weekly refused,
    burst_locked refused.
    """
    with db_transaction(db):
        result = apply_buffer(db, current_user.id, user_mission_id)
    return result


@router.post("/gamification/missions/{user_mission_id}/burst-claim")
def burst_claim_endpoint(
    user_mission_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Claim newly-unlocked Burst paliers (XP only, 0 CAB).

    First claim flips ``burst_locked = true`` permanently.
    """
    with db_transaction(db):
        result = claim_burst(db, current_user.id, user_mission_id)
    return result


@router.post("/gamification/missions/{user_mission_id}/freeze")
def freeze_mission_endpoint(
    user_mission_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Freeze a mission — debit CABs, postpone to next period."""
    cfg = request.app.state.cfg
    freeze_cost = cfg.get("gamification", {}).get("freeze_cost_cab", 100)
    try:
        with db_transaction(db):
            result = freeze_mission(db, current_user.id, user_mission_id, freeze_cost)
    except InsufficientBalance:
        raise HTTPException(status_code=422, detail="insufficient_cab_balance")
    return result
