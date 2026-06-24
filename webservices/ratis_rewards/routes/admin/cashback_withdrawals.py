"""Admin cashback-withdrawals endpoints — list / validate / refuse.

Cashback withdrawals are real-money payouts (Stripe Payouts in prod, sandbox
returns ``sandbox-<uuid>`` when ``PAYMENT_PROVIDER_KEY`` is unset). All
mutations are financial-sensitive and require both ``ADMIN_API_KEY`` and
``X-Admin-TOTP`` (``verify_totp_dep``, see PR2 infra). Listing is read-only :
``ADMIN_API_KEY`` only, no TOTP.

Status mapping (DB ↔ admin action) :
    pending   → no admin action yet
    processed ← validate (payout succeeded, ref + processed_at recorded)
    failed    ← refuse   (failure_reason mandatory ; balance optionally refunded)

Idempotency : both validate and refuse take a row-level lock
(``SELECT ... FOR UPDATE``) and raise 409 ``already_resolved`` if the row is
not ``pending``. This protects against double-actions when two ops click at
the same time.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.exceptions import Conflict, NotFound
from ratis_core.payout_client import PayoutError
from repositories.cashback_repository import list_withdrawals
from services.cashback_service import (
    admin_refuse_withdrawal,
    admin_validate_withdrawal,
)
from services.totp_service import verify_totp_dep
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /admin/cashback/withdrawals — read-only listing
# ---------------------------------------------------------------------------
WithdrawalStatus = Literal["pending", "processed", "failed"]


@router.get(
    "/admin/cashback/withdrawals",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_withdrawals(
    status_filter: WithdrawalStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated list of cashback withdrawals (read-only audit).

    Query params :
        - ``status`` : pending | processed | failed (optional)
        - ``limit`` (1..500, default 50), ``offset`` (default 0)

    Returns ``{ "withdrawals": [...], "total": <count_after_filter> }``.
    """
    rows, total = list_withdrawals(db, status=status_filter, limit=limit, offset=offset)
    return {
        "withdrawals": [
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]),
                "amount": r["amount"],
                "status": r["status"],
                "payment_provider_ref": r["payment_provider_ref"],
                "provider_initiated_at": (
                    r["provider_initiated_at"].isoformat() if r["provider_initiated_at"] else None
                ),
                "requested_at": (r["requested_at"].isoformat() if r["requested_at"] else None),
                "processed_at": (r["processed_at"].isoformat() if r["processed_at"] else None),
                "failure_reason": r["failure_reason"],
                "last_reconciled_at": (r["last_reconciled_at"].isoformat() if r["last_reconciled_at"] else None),
            }
            for r in rows
        ],
        "total": total,
    }


# ---------------------------------------------------------------------------
# PATCH /admin/cashback/withdrawals/{id}/validate — calls payout provider
# ---------------------------------------------------------------------------
class ValidateWithdrawalRequest(BaseModel):
    """Optional notes — currently unused but reserved for future audit log."""

    notes: str | None = Field(default=None, max_length=500)


@router.patch(
    "/admin/cashback/withdrawals/{withdrawal_id}/validate",
    status_code=200,
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def admin_validate(
    withdrawal_id: uuid.UUID,
    body: ValidateWithdrawalRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Validate a pending withdrawal — initiate payout, record provider ref.

    Errors :
        - 401 ``totp_required`` / ``totp_invalid`` — 2FA failure
        - 403 ``forbidden`` — wrong ADMIN_API_KEY
        - 404 ``withdrawal_not_found``
        - 409 ``already_resolved`` — status != 'pending'
        - 503 ``payment_provider_unavailable`` — Stripe call failed
    """
    try:
        with db_transaction(db):
            result = admin_validate_withdrawal(db, withdrawal_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail)
    except PayoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="payment_provider_unavailable",
        )
    return {
        "id": str(result["id"]),
        "status": result["status"],
        "payment_provider_ref": result["payment_provider_ref"],
    }


# ---------------------------------------------------------------------------
# PATCH /admin/cashback/withdrawals/{id}/refuse — failure_reason mandatory
# ---------------------------------------------------------------------------
class RefuseWithdrawalRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    refund_balance: bool = True


@router.patch(
    "/admin/cashback/withdrawals/{withdrawal_id}/refuse",
    status_code=200,
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def admin_refuse(
    withdrawal_id: uuid.UUID,
    body: RefuseWithdrawalRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Refuse a pending withdrawal — log reason, optionally refund balance.

    Body :
        - ``reason`` : 3..200 chars (stored in ``failure_reason``).
        - ``refund_balance`` : default ``true`` — adds ``amount`` back to
          ``user_cashback_balance`` (the user originally paid that amount
          when they POSTed /rewards/cashback/withdraw, so refusing without
          refund effectively confiscates the cashback — only do that when
          fraud is suspected).
    """
    try:
        with db_transaction(db):
            result = admin_refuse_withdrawal(
                db,
                withdrawal_id,
                reason=body.reason,
                refund_balance=body.refund_balance,
            )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail)
    return {
        "id": str(result["id"]),
        "status": result["status"],
        "refunded": result["refunded"],
    }
