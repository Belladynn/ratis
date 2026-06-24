"""
Referral routes — user-facing + internal webhook.

- GET  /rewards/referral/code     (JWT)           : return the user's code,
                                                     lazy-creating it.
- GET  /rewards/referral/history  (JWT)           : list of filleuls + stats.
- POST /rewards/referral/trigger  (INTERNAL_KEY)  : called fire-and-forget by
                                                     ratis_auth Stripe webhook
                                                     when a filleul subscribes.

Shared logic lives in ``services.referral_service``. Routes are thin HTTP
wrappers that parse requests and commit transactions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from ratis_core.database import get_db
from ratis_core.deps import verify_internal_key
from ratis_core.models.user import User
from services.referral_service import (
    award_referral_signup_bonus,
    get_history,
    get_or_create_code,
    handle_subscription_referral,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ============================================================
# USER-FACING — GET /rewards/referral/{code,history}
# ============================================================


class ReferralCodeResponse(BaseModel):
    code: str
    created_at: datetime


@router.get("/rewards/referral/code", response_model=ReferralCodeResponse)
def get_referral_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the current user's referral code (lazy-creates on first access)."""
    with db_transaction(db):
        return get_or_create_code(db, current_user.id)


class ReferralHistoryUse(BaseModel):
    referred_user_display_name: str | None
    plan: str | None
    status: str
    rewarded_at: datetime | None
    created_at: datetime


class ReferralHistoryStats(BaseModel):
    total_uses: int
    rewarded_uses: int
    total_cab_earned: int


class ReferralHistoryResponse(BaseModel):
    code: str
    stats: ReferralHistoryStats
    uses: list[ReferralHistoryUse]


@router.get("/rewards/referral/history", response_model=ReferralHistoryResponse)
def get_referral_history(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the user's referral history (list of filleuls) + aggregated stats."""
    cfg = request.app.state.cfg
    with db_transaction(db):
        return get_history(db, current_user.id, cfg.get("rewards", {}))


# ============================================================
# INTERNAL — POST /rewards/referral/trigger
# ============================================================


class ReferralTriggerRequest(BaseModel):
    referred_user_id: uuid.UUID
    plan: Literal["monthly", "annual"]


@router.post(
    "/rewards/referral/trigger",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def referral_trigger(
    body: ReferralTriggerRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Award CAB + XP + gift card to the referrer upon referred user subscription."""
    cfg = request.app.state.cfg
    with db_transaction(db):
        handle_subscription_referral(db, body.referred_user_id, body.plan, cfg)
    return {"ok": True}


class ReferralSignupBonusRequest(BaseModel):
    referred_user_id: uuid.UUID


@router.post(
    "/rewards/referral/signup-bonus",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def referral_signup_bonus(
    body: ReferralSignupBonusRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """
    Award Y's +150 CAB signup bonus. Called by ratis_auth after a successful
    register() with a valid referral_code. Idempotent via transaction lookup.
    """
    cfg = request.app.state.cfg
    with db_transaction(db):
        awarded = award_referral_signup_bonus(db, body.referred_user_id, cfg)
    return {"ok": True, "awarded": awarded}
