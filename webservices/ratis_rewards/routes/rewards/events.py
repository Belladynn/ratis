"""
POST /rewards/events/action — internal, INTERNAL_API_KEY required.

Phase B (PR #325) generic gamification event endpoint that supersedes
the V0 ``/rewards/events/scan_accepted`` route. The caller (PA worker,
LO worker, batch jobs…) emits events through
``ratis_core.rewards_client.trigger_action``. The server records the
event in ``reward_events`` (idempotency keyed on ``idempotency_key``),
then awards CAB / XP and progresses missions in a single transaction.
"""

from __future__ import annotations

import uuid
from typing import Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_internal_key
from services.events_service import handle_action
from sqlalchemy.orm import Session

router = APIRouter()


# Whitelist of accepted action_types — kept in sync with
# ``services.events_service._KNOWN_ACTION_TYPES`` and the DB CHECK
# constraint on ``missions.action_type``. Pydantic enforces it at the
# route boundary so any unknown value returns 422 before the service
# layer ever runs.
ActionType = Literal[
    "receipt_scan",
    "label_scan",
    "product_identification",
    "fill_product_field",
    "scan_distinct",
    "promo_found",
    "price_compared",
]


class TriggerActionRequest(BaseModel):
    """Payload for POST /rewards/events/action.

    ``quantity`` is constrained to 1..100 by Pydantic — zero, negative or
    an absurdly large batch returns 422 before the route handler runs.
    The upper bound caps the CAB / XP a single call can mint.
    """

    user_id: uuid.UUID
    action_type: ActionType
    quantity: int = Field(default=1, ge=1, le=100)
    qualifier: str | None = None
    idempotency_key: str | None = None
    context: dict | None = None


@router.post(
    "/rewards/events/action",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def trigger_action_endpoint(
    body: TriggerActionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Generic gamification event ingestion.

    Idempotency : two POSTs sharing the same ``idempotency_key`` award
    CAB / XP exactly once. The duplicate is logged in ``reward_events``
    with ``status='duplicate'`` for forensics.
    """
    cfg = request.app.state.cfg
    rewards_cfg = cfg["rewards"]
    xp_cfg = cfg.get("xp", {})

    with db_transaction(db):
        result = handle_action(
            db,
            user_id=body.user_id,
            action_type=body.action_type,
            quantity=body.quantity,
            qualifier=body.qualifier,
            idempotency_key=body.idempotency_key,
            context=body.context,
            rewards_cfg=rewards_cfg,
            xp_cfg=xp_cfg,
        )
    return {"ok": True, **result}
