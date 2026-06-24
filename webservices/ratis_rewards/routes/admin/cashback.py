"""
Admin cashback endpoints.

PATCH /admin/cashback/{id}/validate   (ADMIN_API_KEY)
PATCH /admin/cashback/{id}/refuse     (ADMIN_API_KEY)
POST  /admin/affiliate-offers         (ADMIN_API_KEY)
GET   /admin/affiliate-offers         (ADMIN_API_KEY)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.exceptions import Conflict, NotFound
from repositories.cashback_repository import create_affiliate_offer, get_all_affiliate_offers
from services.cashback_service import resolve_cashback
from sqlalchemy.orm import Session

router = APIRouter()


@router.patch(
    "/admin/cashback/{transaction_id}/validate",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_validate(
    transaction_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Confirm a cashback CREDIT transaction."""
    rewards_cfg = request.app.state.cfg["rewards"]
    try:
        with db_transaction(db):
            resolve_cashback(db, transaction_id, "confirmed", rewards_cfg)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail)
    return {"ok": True}


@router.patch(
    "/admin/cashback/{transaction_id}/refuse",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_refuse(
    transaction_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Refuse a cashback CREDIT transaction."""
    rewards_cfg = request.app.state.cfg["rewards"]
    try:
        with db_transaction(db):
            resolve_cashback(db, transaction_id, "refused", rewards_cfg)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail)
    return {"ok": True}


class CreateAffiliateOfferRequest(BaseModel):
    provider: Literal["affilae", "awin", "cj", "direct"]
    external_id: str = Field(..., min_length=1)
    product_ean: str = Field(..., min_length=1)
    brand_id: uuid.UUID
    cashback_rate: Decimal = Field(..., gt=0, le=1)
    valid_from: datetime
    valid_until: datetime | None = None


@router.post(
    "/admin/affiliate-offers",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_offer(
    body: CreateAffiliateOfferRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Create a new affiliate offer."""
    with db_transaction(db):
        offer_id = create_affiliate_offer(
            db,
            provider=body.provider,
            external_id=body.external_id,
            product_ean=body.product_ean,
            brand_id=body.brand_id,
            cashback_rate=body.cashback_rate,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
        )
    return {"id": str(offer_id)}


@router.get(
    "/admin/affiliate-offers",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_offers(db: Session = Depends(get_db)) -> list[dict]:
    """List all affiliate offers."""
    with db_transaction(db):
        offers = get_all_affiliate_offers(db)
    return [
        {
            "id": str(o["id"]),
            "provider": o["provider"],
            "external_id": o["external_id"],
            "product_ean": o["product_ean"],
            "brand_id": str(o["brand_id"]),
            "cashback_rate": str(o["cashback_rate"]),
            "valid_from": o["valid_from"].isoformat() if o["valid_from"] else None,
            "valid_until": o["valid_until"].isoformat() if o["valid_until"] else None,
            "created_at": o["created_at"].isoformat() if o["created_at"] else None,
        }
        for o in offers
    ]
