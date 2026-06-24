"""Admin CAB endpoints — manual adjustment + transactions audit.

Financial-sensitive endpoints :

- ``POST /admin/cab/adjustment`` — mutation : require both
  ``ADMIN_API_KEY`` (always) and ``X-Admin-TOTP`` (2FA, ``verify_totp_dep``).
  Audit trail : every mutation logged in ``cabecoin_transactions.context``
  with the ``X-Admin-Operator`` handle and the human reason.

- ``GET /admin/cab/users/{user_id}/transactions`` — read-only audit :
  ``ADMIN_API_KEY`` only (no TOTP — read-only is not financially mutative).

Reference scheme : every admin-credited or admin-debited row carries
``reference_type='admin'`` + ``reference_id=<uuid4>`` (the row's own
audit handle, generated server-side ; the row's PK is also unique but
keeping ``reference_id`` separate matches the schema convention used by
all other reference_types).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.models.gamification import CabecoinsTransaction
from repositories.cab_repository import (
    InsufficientBalance,
    admin_adjust_cab,
)
from services.totp_service import verify_totp_dep
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /admin/cab/adjustment
# ---------------------------------------------------------------------------
class CabAdjustmentRequest(BaseModel):
    user_id: uuid.UUID
    direction: Literal["credit", "debit"]
    amount_cents: int = Field(gt=0, description="Positive amount in CAB units")
    reason: str = Field(min_length=3, max_length=200)


@router.post(
    "/admin/cab/adjustment",
    status_code=200,
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def admin_cab_adjustment(
    body: CabAdjustmentRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict:
    """Manual CAB credit/debit by an admin.

    Side effects (atomic) :
        - INSERT cabecoin_transactions
            reason='admin_adjustment',
            reference_type='admin',
            reference_id=<uuid4>,
            context={operator: <X-Admin-Operator>, reason: <body.reason>}
        - UPDATE user_cab_balance atomically (R09 — UPDATE-atomic mandatory)

    Errors :
        - 401 ``totp_required`` / ``totp_invalid`` — 2FA failure (verify_totp_dep)
        - 403 ``forbidden`` — wrong ADMIN_API_KEY (verify_admin_key)
        - 404 ``user_not_found`` — user_id has no balance row
        - 409 ``insufficient_balance`` — debit larger than current balance
    """
    try:
        with db_transaction(db):
            tx_id = admin_adjust_cab(
                db,
                user_id=body.user_id,
                direction=body.direction,
                amount=body.amount_cents,
                operator=x_admin_operator,
                operator_reason=body.reason,
            )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    except InsufficientBalance:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="insufficient_balance")
    return {"transaction_id": str(tx_id), "status": "ok"}


# ---------------------------------------------------------------------------
# GET /admin/cab/users/{user_id}/transactions
# ---------------------------------------------------------------------------
@router.get(
    "/admin/cab/users/{user_id}/transactions",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_cab_user_transactions(
    user_id: uuid.UUID,
    direction: Literal["credit", "debit"] | None = None,
    reference_type: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated list of CAB transactions for a user — read-only audit.

    Query params (all optional) :
        - direction : 'credit' | 'debit'
        - reference_type : filter by reference_type
        - since : ISO datetime, only rows with created_at >= since
        - limit (1..500, default 100), offset (default 0)

    Returns :
        ``{ "transactions": [...], "total": <count_after_filters> }``
    """
    # Build typed filters via SQLAlchemy core — no f-string SQL ; safe by
    # construction (S608-clean, no user input ever reaches the SQL string).
    conditions = [CabecoinsTransaction.user_id == user_id]
    if direction is not None:
        conditions.append(CabecoinsTransaction.direction == direction)
    if reference_type is not None:
        conditions.append(CabecoinsTransaction.reference_type == reference_type)
    if since is not None:
        conditions.append(CabecoinsTransaction.created_at >= since)

    where_clause = and_(*conditions)

    total = db.execute(select(func.count()).select_from(CabecoinsTransaction).where(where_clause)).scalar_one()

    rows = db.execute(
        select(
            CabecoinsTransaction.id,
            CabecoinsTransaction.direction,
            CabecoinsTransaction.amount,
            CabecoinsTransaction.reason,
            CabecoinsTransaction.reference_id,
            CabecoinsTransaction.reference_type,
            CabecoinsTransaction.context,
            CabecoinsTransaction.created_at,
        )
        .where(where_clause)
        .order_by(
            desc(CabecoinsTransaction.created_at),
            desc(CabecoinsTransaction.id),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "transactions": [
            {
                "id": str(r.id),
                "direction": r.direction,
                "amount": r.amount,
                "reason": r.reason,
                "reference_id": str(r.reference_id) if r.reference_id else None,
                "reference_type": r.reference_type,
                "context": r.context,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": int(total),
    }
