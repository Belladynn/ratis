"""
Cashback endpoints — user-facing and internal.

GET  /rewards/cashback/balance              (JWT)
POST /rewards/cashback/scan-detected        (INTERNAL_API_KEY)
POST /rewards/cashback/boost/{tx_id}        (JWT)
POST /rewards/cashback/process-retroactive  (INTERNAL_API_KEY) — PR-B

Amounts in responses are INTEGER centimes. Frontend divides by 100 for display.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_internal_key
from ratis_core.exceptions import NotFound
from repositories.cab_repository import InsufficientBalance
from repositories.cashback_repository import (
    confirm_store_receipts,
    get_cashback_balance,
    get_pending_credits,
    get_pending_store_receipt_ids,
    get_pending_store_receipt_scans,
)
from services.cashback_service import AlreadyBoosted, BoostWindowExpired, boost_cashback, detect_cashback
from sqlalchemy.orm import Session

router = APIRouter()


class ReceiptLine(BaseModel):
    ean: str
    price: int = Field(..., ge=0)  # centimes
    scan_id: uuid.UUID


class ScanDetectedRequest(BaseModel):
    user_id: uuid.UUID
    receipt_lines: list[ReceiptLine]


@router.post(
    "/rewards/cashback/scan-detected",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def scan_detected(
    body: ScanDetectedRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Internal endpoint — called by ratis_product_analyser for receipt scans."""
    rewards_cfg = request.app.state.cfg["rewards"]
    lines = [{"ean": line.ean, "price": line.price, "scan_id": line.scan_id} for line in body.receipt_lines]
    with db_transaction(db):
        detect_cashback(db, body.user_id, lines, rewards_cfg)
    return {"ok": True}


@router.get("/rewards/cashback/balance")
def get_balance(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    rewards_cfg = request.app.state.cfg["rewards"]
    user_id = current_user.id
    balance = get_cashback_balance(db, user_id)  # int centimes
    pending_rows = get_pending_credits(db, user_id)

    boost_window_hours = rewards_cfg["cashback_boost_window_hours"]
    boost_cab_rate = rewards_cfg["cashback_boost_cab_rate"]
    now = datetime.now(UTC)

    pending: list[dict[str, Any]] = []
    for row in pending_rows:
        item: dict[str, Any] = {
            "id": str(row["id"]),
            "amount": row["amount"],  # int centimes
            "product_ean": row["product_ean"],
            "status": row["status"],
        }
        if not row["boost_applied"]:
            created_at = row["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            boost_until = created_at + timedelta(hours=boost_window_hours)
            if now < boost_until:
                item["boost_available_until"] = boost_until.isoformat()
                # Decimal end-to-end (KP-03). Must mirror exactly the
                # cost computed in cashback_service.boost_cashback so the
                # quoted price equals the price actually charged.
                item["boost_cost_cab"] = int(
                    (Decimal(row["amount"]) * Decimal(str(boost_cab_rate))).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )
        pending.append(item)

    return {"cashback_balance": balance, "pending": pending}


class ProcessRetroactiveRequest(BaseModel):
    store_id: uuid.UUID


@router.post(
    "/rewards/cashback/process-retroactive",
    status_code=200,
    dependencies=[Depends(verify_internal_key)],
)
def process_retroactive(
    body: ProcessRetroactiveRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Internal — credit cashback for receipts attached to a store that just
    flipped from validation_status='pending' to 'confirmed'.

    Picks every receipt with ``store_id=:sid AND store_status='pending'``,
    detects cashback offers on each receipt's accepted scans (reusing
    ``detect_cashback``, which is idempotent per (scan_id, ean)), then flips
    the receipt to ``store_status='confirmed'``.

    Idempotent: the WHERE filter excludes receipts already processed.
    See ARCH_store_validation.md § Cashback rétroactif.
    """
    rewards_cfg = request.app.state.cfg["rewards"]
    store_id = body.store_id

    # Gather all pending receipts for this store. We pull (user_id, scan rows)
    # in one go to avoid N+1 across users.
    pending = get_pending_store_receipt_scans(db, store_id)

    by_user: dict[uuid.UUID, list[dict]] = {}
    for row in pending:
        by_user.setdefault(row["user_id"], []).append(
            {
                "ean": row["product_ean"],
                "price": int(row["price"]),
                "scan_id": row["scan_id"],
            }
        )

    total_cashback_cents = 0
    with db_transaction(db):
        # detect_cashback returns the centimes it actually inserted on this
        # call — summing per-user gives a delta scoped to this store flip,
        # immune to concurrent CREDITs for unrelated users (RW-07).
        for user_id, lines in by_user.items():
            total_cashback_cents += detect_cashback(db, user_id, lines, rewards_cfg)

        # Also gather receipts with NO accepted scans / no offer-eligible items
        # so we still flip their state. We re-query in case the caller has
        # only-rejected-scan receipts attached.
        all_pending_ids = get_pending_store_receipt_ids(db, store_id)
        confirm_store_receipts(db, all_pending_ids)

    return {
        "processed_receipts": len(all_pending_ids),
        "total_cashback_cents": total_cashback_cents,
    }


@router.post("/rewards/cashback/boost/{transaction_id}")
def boost(
    transaction_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    rewards_cfg = request.app.state.cfg["rewards"]
    try:
        with db_transaction(db):
            result = boost_cashback(db, current_user.id, transaction_id, rewards_cfg)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except AlreadyBoosted:
        raise HTTPException(status_code=409, detail="already_boosted")
    except BoostWindowExpired:
        raise HTTPException(status_code=409, detail="boost_window_expired")
    except InsufficientBalance:
        raise HTTPException(status_code=422, detail="insufficient_cab_balance")
    return {
        "ok": True,
        "boost_amount": result["boost_amount"],  # int centimes
        "boost_cost_cab": result["boost_cost_cab"],
    }
