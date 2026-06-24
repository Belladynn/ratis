"""
Battlepass service — business logic for GET /rewards/battlepass and claim.
"""

from __future__ import annotations

import uuid
from typing import Any

from ratis_core.exceptions import Conflict, Forbidden, NotFound
from repositories.battlepass_repository import (
    get_active_battlepass_data,
    get_milestone_for_claim,
    insert_milestone_claim,
    is_subscriber,
)
from repositories.cab_repository import award_cab, get_balance
from repositories.gift_card_repository import insert_gift_card_order
from repositories.xp_repository import award_xp
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def get_battlepass(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """Assemble the GET /rewards/battlepass response."""
    data = get_active_battlepass_data(db, user_id)
    if not data:
        return {"season": None}

    season = data["season"]
    return {
        "season": {
            "id": season["id"],
            "name": season["name"],
            "ends_at": season["ends_at"].isoformat(),
        },
        "cab_earned_season": data["cab_earned_season"],
        "milestones": [
            {
                "id": m["id"],
                "milestone_number": m["milestone_number"],
                "cab_required": m["cab_required"],
                "reward_type": m["reward_type"],
                "reward_value": m["reward_value"],
                "subscriber_only": m["subscriber_only"],
                "status": m["status"],
            }
            for m in data["milestones"]
        ],
    }


def claim_milestone(
    db: Session,
    user_id: uuid.UUID,
    milestone_id: uuid.UUID,
    gift_card_cfg: dict[str, Any] | None = None,
    xp_per_milestone: int = 0,
) -> dict[str, Any]:
    """
    Claim a battlepass milestone.

    Validates status and subscriber access, inserts the claim, and optionally
    awards CAB — all within the caller's transaction.

    Raises domain exceptions (NotFound, Conflict, Forbidden) for all error cases.
    """
    milestone = get_milestone_for_claim(db, user_id, milestone_id)
    if milestone is None:
        raise NotFound("milestone_not_found")

    if milestone["status"] == "claimed":
        raise Conflict("milestone_already_claimed")

    if milestone["status"] == "locked":
        raise Forbidden("milestone_locked")

    if milestone["subscriber_only"] and not is_subscriber(db, user_id):
        raise Forbidden("subscriber_required")

    try:
        insert_milestone_claim(db, user_id, milestone_id)
    except IntegrityError:
        raise Conflict("milestone_already_claimed")

    gift_card_order_id: uuid.UUID | None = None

    if milestone["reward_type"] == "cab":
        # apply_to_bp_progress=False : the CAB rewarded by claiming a BP milestone
        # MUST NOT feed back into cab_earned_season — otherwise milestone N would
        # unlock milestone N+1 "for free" (self-feeding loop). Bug archi acted
        # 2026-05-08.
        award_cab(
            db,
            user_id,
            milestone["reward_value"],
            "battlepass_milestone",
            reference_id=milestone_id,
            reference_type="battlepass_milestone",
            apply_to_bp_progress=False,
        )
    elif milestone["reward_type"] == "gift_card" and gift_card_cfg:
        brand_id_str = gift_card_cfg.get("battlepass_brand_id", "")
        if brand_id_str:
            gift_card_order_id = insert_gift_card_order(
                db,
                user_id=user_id,
                brand_id=uuid.UUID(brand_id_str),
                denomination_cents=milestone["reward_value"],
                source_type="battlepass_milestone",
                source_ref_id=str(milestone_id),
            )

    if xp_per_milestone > 0:
        award_xp(
            db,
            user_id,
            xp_per_milestone,
            "battlepass_milestone",
            reference_id=milestone_id,
            reference_type="battlepass_milestone",
        )

    return {
        "claimed": True,
        "reward_type": milestone["reward_type"],
        "reward_value": milestone["reward_value"],
        "new_cab_balance": get_balance(db, user_id),
        "gift_card_order_id": gift_card_order_id,
    }
