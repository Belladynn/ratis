"""
POST /rewards/cashback/withdraw — user-initiated cashback withdrawal.

Amount in request and response is INTEGER centimes.

Flow:
  1. Validate amount >= cashback_min_withdrawal
  2. DB transaction: debit balance + INSERT cashback_transactions (WITHDRAWAL) +
     INSERT cashback_withdrawals (pending, no ref yet)
  3. commit()
  4. Call payout provider (Stripe or sandbox) → get payment_provider_ref
  5. UPDATE cashback_withdrawals SET payment_provider_ref + provider_initiated_at
  6. commit()

If step 4 fails: withdrawal stays pending with NULL payment_provider_ref.
ratis_batch_reconciliation will detect it (pending > 24h, NULL ref) and retry.

If step 5-6 fails (DB write after successful payout): ref is lost but withdrawal is
still pending — reconciliation batch detects it via NULL ref + logged ERROR.
"""

from __future__ import annotations

import logging

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.payout_client import PayoutError, initiate_payout
from repositories.cashback_repository import (
    InsufficientCashbackBalance,
    set_withdrawal_provider_ref,
)
from services.cashback_service import withdraw_cashback
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
router = APIRouter()


class WithdrawRequest(BaseModel):
    amount: int = Field(..., gt=0)  # centimes


@router.post("/rewards/cashback/withdraw", status_code=201)
def withdraw(
    body: WithdrawRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    rewards_cfg = request.app.state.cfg["rewards"]
    if body.amount < rewards_cfg["cashback_min_withdrawal"]:
        raise HTTPException(status_code=422, detail="below_minimum")

    # Step 1-3: DB transaction (debit + records)
    try:
        with db_transaction(db):
            result = withdraw_cashback(db, current_user.id, body.amount, rewards_cfg)
    except InsufficientCashbackBalance:
        raise HTTPException(status_code=422, detail="insufficient_balance")

    withdrawal_id = result["withdrawal_id"]

    # Step 4: Call provider (payout not yet sent to bank if this raises)
    try:
        ref = initiate_payout(withdrawal_id, body.amount)
    except PayoutError as exc:
        log.error(
            "withdraw: payout failed for withdrawal %s — will be retried by reconciliation batch: %s",
            withdrawal_id,
            exc,
        )
        return {
            "withdrawal_id": str(withdrawal_id),
            "amount": result["amount"],
            "status": "pending",
        }

    # Step 5-6: Store ref — payout already sent, log loudly if this fails
    # (money left the system but ref not persisted — reconciliation batch must recover)
    try:
        with db_transaction(db):
            set_withdrawal_provider_ref(db, withdrawal_id, ref)
    except Exception as exc:
        log.error(
            "withdraw: CRITICAL — payout ref storage failed for withdrawal %s (ref=%s) — "
            "payout was sent but ref not persisted; reconciliation batch must recover: %s",
            withdrawal_id,
            ref,
            exc,
            exc_info=True,
        )

    return {
        "withdrawal_id": str(withdrawal_id),
        "amount": result["amount"],
        "status": "pending",
    }
