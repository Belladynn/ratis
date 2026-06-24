"""
GET  /gamification/battlepass                       — user-facing, JWT auth required.
POST /gamification/battlepass/claim/{milestone_id}  — user-facing, JWT auth required.
"""

from __future__ import annotations

import uuid

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from ratis_core.database import get_db
from ratis_core.models.user import User
from services.battlepass_service import claim_milestone, get_battlepass
from services.gift_card_service import issue_gift_card_bg
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/gamification/battlepass")
def get_battlepass_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the active battlepass season with milestones and computed statuses."""
    with db_transaction(db):
        result = get_battlepass(db, current_user.id)
    return result


@router.post("/gamification/battlepass/claim/{milestone_id}", status_code=200)
def claim_battlepass_milestone(
    milestone_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Claim a battlepass milestone reward."""
    cfg = request.app.state.cfg
    gift_card_cfg = cfg.get("gift_cards")
    xp_per_milestone = cfg.get("xp", {}).get("xp_per_battlepass_milestone", 0)
    with db_transaction(db):
        result = claim_milestone(
            db,
            current_user.id,
            milestone_id,
            gift_card_cfg=gift_card_cfg,
            xp_per_milestone=xp_per_milestone,
        )
    if result.get("reward_type") == "gift_card" and result.get("gift_card_order_id"):
        background_tasks.add_task(issue_gift_card_bg, result["gift_card_order_id"])
    return result
