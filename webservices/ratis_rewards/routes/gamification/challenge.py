"""
GET  /api/v1/gamification/challenge                            — état courant du défi actif
POST /api/v1/gamification/challenge/milestones/{milestone_id}/claim — réclamer un palier
"""

from __future__ import annotations

import uuid

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from ratis_core.database import get_db
from ratis_core.models.user import User
from repositories.cab_repository import award_cab
from repositories.challenge_repository import (
    claim_milestone,
    create_community_multiplier,
    get_active_challenge_with_state,
)
from repositories.exceptions import (
    ChallengeExpired,
    MilestoneAlreadyClaimed,
    MilestoneLocked,
    MilestoneNotFound,
)
from repositories.xp_repository import award_xp
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/gamification/challenge")
def get_challenge(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Return the active (or frozen) community challenge with per-user milestone state.

    Returns 404 when no challenge is active or when the challenge has expired
    (past ends_at + grace_period_days).
    """
    state = get_active_challenge_with_state(db, current_user.id)
    if state is None:
        raise HTTPException(status_code=404, detail="challenge_not_found")
    return state


@router.post("/gamification/challenge/milestones/{milestone_id}/claim")
def claim_challenge_milestone(
    milestone_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Claim a community challenge milestone.

    - 404 milestone_not_found    — milestone doesn't exist or belongs to no active challenge
    - 409 challenge_expired      — past ends_at + grace_period_days
    - 409 milestone_locked       — community progress hasn't reached this threshold
    - 409 milestone_already_claimed — user has already claimed this milestone
    """
    try:
        with db_transaction(db):
            result = claim_milestone(db, current_user.id, milestone_id)
            _apply_reward(db, current_user.id, result, milestone_id)
    except MilestoneNotFound:
        raise HTTPException(status_code=404, detail="milestone_not_found")
    except ChallengeExpired:
        raise HTTPException(status_code=409, detail="challenge_expired")
    except MilestoneLocked:
        raise HTTPException(status_code=409, detail="milestone_locked")
    except MilestoneAlreadyClaimed:
        raise HTTPException(status_code=409, detail="milestone_already_claimed")
    return {
        "milestone_id": result["milestone_id"],
        "reward_type": result["reward_type"],
        "reward_value": result["reward_value"],
    }


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _apply_reward(
    db,
    user_id: uuid.UUID,
    result: dict,
    milestone_id: uuid.UUID,
) -> None:
    """
    Apply the milestone reward within the route's db_transaction.

    Kept in the route layer so challenge_repository has no dependency on
    cab_repository or xp_repository (DA-14 / KP-22).
    """
    reward_type = result["reward_type"]
    reward_value = result["reward_value"]
    challenge_id = result["challenge_id"]

    if reward_type == "cab":
        amount = reward_value.get("amount", 0)
        if amount > 0:
            award_cab(
                db,
                user_id,
                amount,
                "challenge_milestone",
                reference_id=milestone_id,
                reference_type="community_challenge_milestone",
                apply_streak_multiplier=False,
            )
    elif reward_type == "xp":
        amount = reward_value.get("amount", 0)
        if amount > 0:
            award_xp(
                db,
                user_id,
                amount,
                "challenge_milestone",
                reference_id=milestone_id,
                reference_type="community_challenge_milestone",
                apply_streak_multiplier=False,
            )
    elif reward_type == "multiplier":
        multiplier = float(reward_value.get("multiplier", 0.0))
        duration_hours = int(reward_value.get("duration_hours", 0))
        applies_to = reward_value.get("applies_to", "both")
        if multiplier > 0 and duration_hours > 0:
            create_community_multiplier(db, challenge_id, user_id, multiplier, applies_to, duration_hours)
    # 'skin': no-op until cosmetics system is implemented (see PROD_CHECKLIST)
