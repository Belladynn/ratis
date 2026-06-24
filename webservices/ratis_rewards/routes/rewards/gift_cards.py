"""
GET  /rewards/gift-cards            — list user's gift card orders
GET  /rewards/gift-cards/{id}       — single order detail
GET  /rewards/gift-cards/catalog    — boutique catalogue (active brands + caps)
POST /rewards/gift-cards/order      — boutique : spend CAB on a gift card
POST /rewards/gift-cards/annual     — internal: create pending order for annual sub
POST /rewards/gift-cards/{id}/issue — internal: kick off Runa for a pending order

code is only exposed when status == 'issued'.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from deps import get_current_user
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_internal_key
from ratis_core.exceptions import Conflict, NotFound, UnprocessableEntity
from repositories.cab_repository import InsufficientBalance
from repositories.gift_card_repository import (
    get_order,
    get_orders_by_user,
    insert_gift_card_order,
)
from services import boutique_service
from services.gift_card_cap_usage_service import get_cap_usage
from services.gift_card_service import issue_gift_card_bg
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

router = APIRouter()


def _serialize(order: dict) -> dict:
    return {
        "id": str(order["id"]),
        "denomination": order["denomination"],
        "status": order["status"],
        "source_type": order["source_type"],
        "code": order["code"],
        "issued_at": order["issued_at"].isoformat() if order["issued_at"] else None,
        "failed_at": order["failed_at"].isoformat() if order["failed_at"] else None,
        "created_at": order["created_at"].isoformat() if order["created_at"] else None,
        "brand": {
            "id": str(order["brand"]["id"]),
            "name": order["brand"]["name"],
            "logo_url": order["brand"]["logo_url"],
        },
    }


@router.get("/rewards/gift-cards")
def list_gift_cards(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    orders = get_orders_by_user(db, current_user.id)
    return [_serialize(o) for o in orders]


# ---------------------------------------------------------------------------
# Boutique V1 — catalog + order
# Registered BEFORE the {order_id} path so /catalog and /order are not
# captured as UUID parameters (FastAPI matches in declaration order).
# ---------------------------------------------------------------------------


@router.get("/rewards/gift-cards/catalog")
def get_boutique_catalog(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """Return active brands + allowed denominations + ratio."""
    return boutique_service.get_catalog(db)


# Registered BEFORE the {order_id} UUID path so "cap-usage" is not parsed
# as a UUID by FastAPI's path-param coercion (declaration order matters).
@router.get("/rewards/gift-cards/cap-usage")
def read_cap_usage(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """Server-authoritative gift-card cap usage snapshot.

    Replaces the legacy client-side aggregation in
    ``ratis_client/hooks/use-gift-cards.ts:computeUsageStats``. See
    :func:`services.gift_card_cap_usage_service.get_cap_usage` for the
    response shape and per-window semantics.

    Cf F-11 in the V1.1 usage-stats sprint.
    """
    return dict(get_cap_usage(db, current_user.id))


class OrderRequest(BaseModel):
    brand_id: uuid.UUID
    denomination_cents: Literal[500, 1000, 2000, 5000] = Field(
        description="Gift card denomination in cents (5 €, 10 €, 20 € or 50 €)."
    )


class OrderResponse(BaseModel):
    order_id: str
    brand: str
    denomination_cents: int
    cab_cost: int
    new_cab_balance: int
    status: str
    estimated_arrival: str


@router.post(
    "/rewards/gift-cards/order",
    response_model=OrderResponse,
    status_code=201,
)
def create_boutique_order(
    body: OrderRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> OrderResponse:
    """Spend CAB on a gift card. See ARCH_boutique.md for the full contract.

    Status codes :
        201 — order created (pending) ; Runa issuance queued
        400/422 — invalid_denomination
        402 — insufficient_cab_balance
        404 — brand_not_available
        409 — daily_redeem_cap_reached / weekly_redeem_cap_reached
              / annual_gift_card_cap_reached / duplicate_order_recent
    """
    try:
        result = boutique_service.create_order(
            db,
            user_id=current_user.id,
            brand_id=body.brand_id,
            denomination_cents=body.denomination_cents,
        )
    except InsufficientBalance:
        db.rollback()
        raise HTTPException(status_code=402, detail="insufficient_cab_balance")
    except UnprocessableEntity as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.detail)
    except NotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=exc.detail)
    except Conflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.detail)
    except Exception:
        db.rollback()
        raise

    db.commit()
    background_tasks.add_task(issue_gift_card_bg, uuid.UUID(result["order_id"]))
    return OrderResponse(**result)


@router.get("/rewards/gift-cards/{order_id}")
def get_gift_card(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    order = get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="not_found")
    if order["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    return _serialize(order)


class AnnualGiftCardRequest(BaseModel):
    user_id: uuid.UUID
    stripe_session_id: str


@router.post(
    "/rewards/gift-cards/annual",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def create_annual_gift_card(
    body: AnnualGiftCardRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """
    Create a pending gift_card_orders row for an annual subscription.
    Idempotent: same stripe_session_id is a no-op.
    Enqueues Runa call as background task after commit.
    Called fire-and-forget by ratis_auth after checkout.session.completed.
    """
    gift_card_cfg = request.app.state.cfg.get("gift_cards", {})
    brand_id_str = gift_card_cfg.get("annual_subscription_brand_id", "")
    denomination = gift_card_cfg.get("annual_subscription_denomination", 2000)

    # No brand configured → nothing is created. Do NOT claim queued=true
    # (audit RW-money F-6) — the caller would believe a card is on its way
    # when none exists. Report the truthful outcome + log so the missing
    # config surfaces in observability.
    if not brand_id_str:
        log.warning(
            "create_annual_gift_card: annual_subscription_brand_id not "
            "configured — no gift-card order created (user=%s, session=%s)",
            body.user_id,
            body.stripe_session_id,
        )
        return {"queued": False, "reason": "brand_not_configured"}

    order_id = insert_gift_card_order(
        db,
        user_id=body.user_id,
        brand_id=uuid.UUID(brand_id_str),
        denomination_cents=denomination,
        source_type="annual_subscription",
        source_ref_id=body.stripe_session_id,
    )
    db.commit()
    background_tasks.add_task(issue_gift_card_bg, order_id)

    return {"queued": True}


@router.post(
    "/rewards/gift-cards/{order_id}/issue",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def issue_existing_order(
    order_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """
    Kick off Runa issuance for a pre-existing pending order.

    Used by the ratis_batch_referral_payout daily cron after the anti-churn
    30-day delay has passed and the referred user is still subscribed.

    Returns 404 if the order doesn't exist, 409 if it's not in 'pending'
    status (already issued / failed).

    Double-emission safety (audit RW-money F-1) : this status check is a
    cheap fast-fail, NOT the concurrency gate. The authoritative guard is
    the per-order ``pg_advisory_xact_lock`` inside ``issue_gift_card`` —
    even if two ``/issue`` calls both pass the check below and both enqueue
    a background task, the two tasks serialise on that lock and the second
    sees the order is no longer ``pending`` and never calls Runa.
    """
    order = get_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if order["status"] != "pending":
        raise HTTPException(status_code=409, detail="order_not_pending")

    background_tasks.add_task(issue_gift_card_bg, order_id)
    return {"queued": True}
