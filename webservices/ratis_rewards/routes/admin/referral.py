"""
Admin referral datafix endpoint.

POST /admin/referral/link  (ADMIN_API_KEY)

Use case : user Y forgot to enter a referral code at signup, contacts
support. Support staff provides the link via this endpoint, creating the
X→Y association + awarding Y's signup bonus + triggering the X reward if
Y is already subscribed.

See ARCH_referral.md § décisions actées. Replaces the rejected public
``POST /claim`` endpoint — farming protection without sacrificing UX.
"""

from __future__ import annotations

import uuid

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from services.referral_service import link_manually_and_reward
from sqlalchemy.orm import Session

router = APIRouter()


class AdminReferralLinkRequest(BaseModel):
    referred_user_id: uuid.UUID
    code: str = Field(min_length=4, max_length=32)
    admin_operator_id: str = Field(
        min_length=1,
        max_length=128,
        description="Audit trail — who on the support team initiated this link.",
    )


@router.post(
    "/admin/referral/link",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_referral_link(
    body: AdminReferralLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """
    Create a X→Y referral link manually (support-driven datafix).

    Returns a rich dict describing what happened (link created, signup bonus
    awarded, subscription reward triggered if Y was already subscribed).

    Error codes (all map to HTTPException with ``detail`` snake_case) :
      - 400 invalid_code      — code does not exist or is orphaned
      - 400 self_parrainage   — user tried to use their own code
      - 404 user_not_found    — referred_user_id does not exist
      - 409 already_linked    — Y is already associated with another referrer
    """
    cfg = request.app.state.cfg
    with db_transaction(db):
        try:
            return link_manually_and_reward(
                db,
                referred_user_id=body.referred_user_id,
                code=body.code,
                admin_operator_id=body.admin_operator_id,
                cfg=cfg,
            )
        except ValueError as exc:
            code = str(exc)
            status = 404 if code in {"user_not_found"} else (409 if code == "already_linked" else 400)
            raise HTTPException(status_code=status, detail=code) from exc
