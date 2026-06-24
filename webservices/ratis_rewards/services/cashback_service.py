"""
Cashback service — orchestration layer.

All functions operate within the caller's session transaction.
The caller (route handler) is responsible for commit()/rollback().

Amounts are INTEGER centimes throughout (post-migration a8b9c0d1e2f3).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ratis_core.exceptions import Conflict, NotFound
from ratis_core.payout_client import PayoutError, initiate_payout
from repositories.cab_repository import award_cab, debit_cab
from repositories.cashback_repository import (
    credit_cashback_balance,
    debit_cashback_balance,
    get_active_offer_by_ean,
    get_boost_child,
    get_cashback_tx,
    get_withdrawal_for_update,
    has_cashback_for_scan,
    insert_cashback_boost,
    insert_cashback_credit,
    insert_cashback_withdrawal,
    insert_cashback_withdrawal_tx,
    is_user_subscriber,
    mark_boost_applied,
    mark_withdrawal_failed,
    mark_withdrawal_processed,
    update_cashback_tx_distributed,
    update_cashback_tx_status,
)
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class AlreadyBoosted(Exception):
    pass


class BoostWindowExpired(Exception):
    pass


class BelowMinimum(Exception):
    pass


def detect_cashback(
    db: Session,
    user_id: uuid.UUID,
    receipt_lines: list[dict[str, Any]],
    rewards_cfg: dict[str, Any],
) -> int:
    """
    For each receipt line, detect active cashback offers and create CREDIT transactions.

    Each line must include {"ean": str, "price": int (centimes), "scan_id": uuid.UUID}.
    Idempotency is enforced per (scan_id, ean) pair — safe to call multiple times.

    Subscriber: amount is immediately advanced (distributed_at = now(), balance credited).
    Non-subscriber: CREDIT pending, balance unchanged until brand confirms.

    Returns the total centimes of CREDIT rows **inserted by this call** —
    idempotent re-runs (every line already credited) return 0.

    ⚠️  RECONCILIATION SYNC — batch/ratis_batch_reconciliation replicates this logic
    directly in SQL (no HTTP call). Any change to offer detection, subscriber logic,
    or cashback_transactions fields must be reflected in:
        batch/ratis_batch_reconciliation/reconciliation/cashback.py
    """
    subscriber = is_user_subscriber(db, user_id)
    now = datetime.now(UTC) if subscriber else None

    inserted_total = 0
    inserted_any = False
    for line in receipt_lines:
        ean = line["ean"]
        price = int(line["price"])  # centimes
        scan_id = uuid.UUID(str(line["scan_id"]))
        offer = get_active_offer_by_ean(db, ean)
        if offer is None:
            continue
        if has_cashback_for_scan(db, scan_id, ean):
            continue  # idempotent
        # base = rate × price_centimes → centimes. Decimal end-to-end
        # (KP-03 : a money amount must never pass through a binary float).
        # ROUND_HALF_UP so a half-centime always rounds in the user's
        # favour — Python's banker's round() would drop it on a tie.
        base = int((Decimal(offer["cashback_rate"]) * Decimal(price)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        insert_cashback_credit(
            db,
            user_id=user_id,
            offer_id=offer["id"],
            product_ean=ean,
            amount=base,
            scan_id=scan_id,
            distributed_at=now,
        )
        if subscriber:
            credit_cashback_balance(db, user_id, base)
        inserted_total += base
        inserted_any = True

    # Achievements V1 — fire-and-forget once per call after at least one
    # new CREDIT row was inserted. The savings_eur_total handler counts
    # `pending` + `confirmed` rows, so we fire on insert (subscriber and
    # non-subscriber both qualify). Idempotent re-runs (every line already
    # inserted via has_cashback_for_scan) skip the hook entirely.
    if inserted_any:
        try:
            from services import achievement_service

            achievement_service.check_achievements(
                db,
                user_id=user_id,
                event_type="cashback_credited",
                payload={},
            )
        except Exception:
            logger.exception(
                "achievement_hook_cashback_credited_failed",
                extra={"user_id": str(user_id)},
            )

    return inserted_total


def boost_cashback(
    db: Session,
    user_id: uuid.UUID,
    tx_id: uuid.UUID,
    rewards_cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    Apply a boost to a CREDIT cashback transaction.

    Conditions: status != refused, boost_applied = false, within boost window,
    CAB balance >= boost cost.

    Returns {"boost_amount": delta (centimes), "boost_cost_cab": cost}.
    Raises: NotFound, AlreadyBoosted, BoostWindowExpired, InsufficientBalance.
    """
    tx = get_cashback_tx(db, tx_id)
    if tx is None or tx["user_id"] != user_id or tx["type"] != "CREDIT":
        raise NotFound("transaction_not_found")
    if tx["status"] == "refused":
        raise NotFound("transaction_not_found")

    # Cheap read-only pre-check — fail fast on an already-boosted tx
    # without touching the CAB ledger. The authoritative gate is the
    # atomic mark_boost_applied below.
    if tx["boost_applied"]:
        raise AlreadyBoosted()

    window_hours = rewards_cfg["cashback_boost_window_hours"]
    now = datetime.now(UTC)
    created_at = tx["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if now > created_at + timedelta(hours=window_hours):
        raise BoostWindowExpired()

    # ATOMIC GATE (audit RW-money F-4) — claim the boost FIRST, before any
    # money mutation. mark_boost_applied flips boost_applied false→true with
    # the false-guard in its WHERE clause : two concurrent boosters race on
    # this single UPDATE and exactly one wins. The loser sees rowcount 0
    # and aborts here — no double CAB debit, no double BOOST row, no double
    # cashback credit. Ordering it last (the pre-fix shape) left a window
    # where both passed the plain boost_applied check and both paid out.
    if not mark_boost_applied(db, tx_id):
        raise AlreadyBoosted()

    # amount is already in centimes → no ×100 needed. Decimal end-to-end
    # (KP-03) — the config rate is a JSON float, str()-wrapped so its
    # binary representation never leaks into the CAB cost.
    boost_cost_cab = int(
        (Decimal(tx["amount"]) * Decimal(str(rewards_cfg["cashback_boost_cab_rate"]))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    delta = tx["amount"]  # boost doubles cashback: total = base + base; delta = base

    # Raises InsufficientBalance if not enough CAB. The boost_applied flag
    # is already set ; an InsufficientBalance here rolls the whole
    # transaction back (route does db.rollback()), un-setting the flag —
    # so a retry with sufficient balance still works.
    debit_cab(db, user_id, boost_cost_cab, "cashback_boost_debit")

    insert_cashback_boost(
        db,
        user_id=user_id,
        parent_tx_id=tx_id,
        offer_id=tx["affiliate_offer_id"],
        product_ean=tx["product_ean"],
        amount=delta,
    )
    credit_cashback_balance(db, user_id, delta)

    return {"boost_amount": delta, "boost_cost_cab": boost_cost_cab}


def resolve_cashback(
    db: Session,
    tx_id: uuid.UUID,
    resolution: str,
    rewards_cfg: dict[str, Any],
) -> None:
    """
    Resolve a CREDIT cashback transaction as 'confirmed' or 'refused'.

    confirmed:
        - Updates status to confirmed.
        - If not yet distributed (non-subscriber): credits balance + sets distributed_at.

    refused:
        - Updates status to refused.
        - If distributed (subscriber advance): loss absorbed by Ratis — no user debit.
        - If a BOOST child exists: BOOST also refused, CAB refunded to user.
    """
    if resolution not in ("confirmed", "refused"):
        raise ValueError(f"invalid resolution: {resolution!r}")

    tx = get_cashback_tx(db, tx_id)
    if tx is None or tx["type"] != "CREDIT":
        raise NotFound("transaction_not_found")
    if tx["status"] != "pending":
        raise Conflict("already_resolved")

    if resolution == "confirmed":
        # A pending CREDIT with distributed_at already set is a state the
        # business rules forbid (distribution only happens on a subscriber
        # advance, which also confirms, or on this very confirm path).
        # Reaching it means an earlier write left the row inconsistent —
        # surface it loudly rather than silently skipping the credit
        # (audit RW-money F-5).
        if tx["distributed_at"] is not None:
            logger.warning(
                "resolve_cashback_inconsistent_state",
                extra={
                    "tx_id": str(tx_id),
                    "detail": "pending CREDIT already has distributed_at set",
                },
            )
        update_cashback_tx_status(db, tx_id, "confirmed")
        if tx["distributed_at"] is None:
            credit_cashback_balance(db, tx["user_id"], tx["amount"])
            update_cashback_tx_distributed(db, tx_id)

    elif resolution == "refused":
        update_cashback_tx_status(db, tx_id, "refused")
        # distributed_at IS NOT NULL → money already advanced; Ratis absorbs the loss
        # Check for BOOST child → refund CAB
        boost = get_boost_child(db, tx_id)
        if boost:
            update_cashback_tx_status(db, boost["id"], "refused")
            # amount is already centimes → no ×100 needed. Decimal
            # end-to-end (KP-03) — the refund must mirror exactly the
            # cost charged in boost_cashback (same formula, same rounding).
            boost_cost_cab = int(
                (Decimal(boost["amount"]) * Decimal(str(rewards_cfg["cashback_boost_cab_rate"]))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            award_cab(db, boost["user_id"], boost_cost_cab, "cashback_boost_refund")


def admin_validate_withdrawal(
    db: Session,
    withdrawal_id: uuid.UUID,
) -> dict[str, Any]:
    """Validate a pending cashback withdrawal — calls the payout provider.

    Atomicity (within caller's transaction) :
        1. SELECT ... FOR UPDATE (row lock against concurrent admin actions)
        2. NotFound  if the row does not exist
           Conflict  if status != 'pending' (already validated/refused)
        3. ``initiate_payout`` (Stripe in prod, deterministic sandbox-<id>
           when ``PAYMENT_PROVIDER_KEY`` is unset). Provider failures bubble
           up as :class:`ratis_core.payout_client.PayoutError` — the route
           converts to 503 (UpstreamServiceError pattern).
        4. UPDATE status='processed' + processed_at + payment_provider_ref
           + provider_initiated_at (CHECK constraints all satisfied in one UPDATE).

    Returns the post-update snapshot ``{id, status, payment_provider_ref}``.
    """
    row = get_withdrawal_for_update(db, withdrawal_id)
    if row is None:
        raise NotFound("withdrawal_not_found")
    if row["status"] != "pending":
        raise Conflict("already_resolved")

    # Call provider. PayoutError propagates — route maps to 503.
    ref = initiate_payout(withdrawal_id, row["amount"])

    mark_withdrawal_processed(db, withdrawal_id, ref)

    return {
        "id": withdrawal_id,
        "status": "processed",
        "payment_provider_ref": ref,
    }


def admin_refuse_withdrawal(
    db: Session,
    withdrawal_id: uuid.UUID,
    *,
    reason: str,
    refund_balance: bool,
) -> dict[str, Any]:
    """Refuse a pending cashback withdrawal — optionally refund the balance.

    Atomicity (within caller's transaction) :
        1. SELECT ... FOR UPDATE row lock
        2. NotFound  if missing, Conflict  if status != 'pending'
        3. UPDATE status='failed' + failure_reason
        4. If ``refund_balance`` : credit ``user_cashback_balance`` by amount
           (the original ``debit_cashback_balance`` happened on POST /withdraw).

    Returns ``{id, status, refunded}``.
    """
    row = get_withdrawal_for_update(db, withdrawal_id)
    if row is None:
        raise NotFound("withdrawal_not_found")
    if row["status"] != "pending":
        raise Conflict("already_resolved")

    mark_withdrawal_failed(db, withdrawal_id, reason)
    if refund_balance:
        credit_cashback_balance(db, row["user_id"], row["amount"])

    return {
        "id": withdrawal_id,
        "status": "failed",
        "refunded": refund_balance,
    }


# ``PayoutError`` is re-exported here so route handlers can import it from a
# single domain module without reaching into ``ratis_core``.
__all__ = (
    "AlreadyBoosted",
    "BelowMinimum",
    "BoostWindowExpired",
    "PayoutError",
    "admin_refuse_withdrawal",
    "admin_validate_withdrawal",
    "boost_cashback",
    "detect_cashback",
    "resolve_cashback",
    "withdraw_cashback",
)


def withdraw_cashback(
    db: Session,
    user_id: uuid.UUID,
    amount: int,
    rewards_cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    Initiate a cashback withdrawal.

    Atomicity (all within caller's transaction):
    1. debit_cashback_balance — atomic UPDATE, raises InsufficientCashbackBalance if insufficient
    2. INSERT cashback_transactions (WITHDRAWAL, status='confirmed') — accounting fact
    3. INSERT cashback_withdrawals (status='pending') — operational record

    The caller commits after this returns, then handles the payment provider call.
    Raises: BelowMinimum, InsufficientCashbackBalance.
    """
    min_withdrawal: int = rewards_cfg["cashback_min_withdrawal"]  # centimes
    if amount < min_withdrawal:
        raise BelowMinimum()

    debit_cashback_balance(db, user_id, amount)
    tx_id = insert_cashback_withdrawal_tx(db, user_id, amount)
    withdrawal_id = insert_cashback_withdrawal(db, user_id, tx_id, amount)

    return {"withdrawal_id": withdrawal_id, "amount": amount}
