"""
Admin community challenge endpoints.

POST   /admin/challenges                          — create challenge
POST   /admin/challenges/{id}/milestones          — add milestone
GET    /admin/challenges                          — list all challenges
PATCH  /admin/challenges/{id}/activate            — set is_active=TRUE
PATCH  /admin/challenges/{id}/deactivate          — set is_active=FALSE

All routes require ADMIN_API_KEY.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from repositories.challenge_repository import (
    activate_challenge,
    create_challenge,
    create_challenge_milestone,
    deactivate_challenge,
    get_challenge_by_id,
    list_challenges_with_state,
)
from repositories.exceptions import (
    ActiveChallengeConflict,
    ChallengeNotFound,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ChallengeCreateRequest(BaseModel):
    title: str
    description: str | None = None
    action_type: str
    action_filter: dict[str, Any] | None = None
    objective: int = Field(..., gt=0)
    starts_at: datetime
    ends_at: datetime
    grace_period_days: int = Field(default=3, ge=0)


class MilestoneCreateRequest(BaseModel):
    threshold: int = Field(..., gt=0)
    reward_type: Literal["cab", "xp", "multiplier", "skin"]
    reward_value: dict[str, Any]
    label: str | None = None
    sort_order: int = Field(default=0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/admin/challenges",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_challenge(
    body: ChallengeCreateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Create a new community challenge (inactive by default)."""
    with db_transaction(db):
        cid = create_challenge(
            db,
            title=body.title,
            description=body.description,
            action_type=body.action_type,
            action_filter=body.action_filter,
            objective=body.objective,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            grace_period_days=body.grace_period_days,
        )
    challenge = get_challenge_by_id(db, cid)
    # The challenge was just created above with this id, so the lookup always
    # resolves — get_challenge_by_id only returns None for an unknown id.
    assert challenge is not None  # freshly created above → always found
    return challenge


@router.post(
    "/admin/challenges/{challenge_id}/milestones",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_milestone(
    challenge_id: uuid.UUID,
    body: MilestoneCreateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Add a milestone to a challenge."""
    try:
        with db_transaction(db):
            mid = create_challenge_milestone(
                db,
                challenge_id=challenge_id,
                threshold=body.threshold,
                reward_type=body.reward_type,
                reward_value=body.reward_value,
                label=body.label,
                sort_order=body.sort_order,
            )
    except ChallengeNotFound:
        raise HTTPException(status_code=404, detail="challenge_not_found")
    return {
        "id": str(mid),
        "challenge_id": str(challenge_id),
        "threshold": body.threshold,
        "reward_type": body.reward_type,
        "reward_value": body.reward_value,
        "label": body.label,
        "sort_order": body.sort_order,
    }


@router.get(
    "/admin/challenges",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_challenges(
    db: Session = Depends(get_db),
) -> list[dict]:
    """List all challenges with computed status, current_count, and milestone_count."""
    return list_challenges_with_state(db)


@router.patch(
    "/admin/challenges/{challenge_id}/activate",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_activate_challenge(
    challenge_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Set is_active=TRUE. Fails with 409 if another challenge is already active."""
    try:
        with db_transaction(db):
            activate_challenge(db, challenge_id)
    except ChallengeNotFound:
        raise HTTPException(status_code=404, detail="challenge_not_found")
    except ActiveChallengeConflict:
        raise HTTPException(status_code=409, detail="active_challenge_conflict")
    return {"ok": True}


@router.patch(
    "/admin/challenges/{challenge_id}/deactivate",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_deactivate_challenge(
    challenge_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Set is_active=FALSE."""
    try:
        with db_transaction(db):
            deactivate_challenge(db, challenge_id)
    except ChallengeNotFound:
        raise HTTPException(status_code=404, detail="challenge_not_found")
    return {"ok": True}
