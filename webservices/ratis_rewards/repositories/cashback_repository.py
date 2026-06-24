"""
Cashback repository — raw SQL for atomicity.

All write operations work within the caller's session transaction.
The caller is responsible for commit() after all operations succeed.

Amounts are INTEGER centimes throughout (post-migration a8b9c0d1e2f3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.exceptions import (
    InsufficientCashbackBalance,
    WithdrawalNotFound,
)

# Re-export so existing imports from this module keep working.
__all__ = ["InsufficientCashbackBalance", "WithdrawalNotFound"]


def get_active_offer_by_ean(db: Session, ean: str) -> dict[str, Any] | None:
    """Return the currently active affiliate offer for an EAN, or None."""
    row = db.execute(
        text(
            "SELECT id, provider, external_id, product_ean, brand_id, cashback_rate "
            "FROM affiliate_offers "
            "WHERE product_ean = :ean "
            "  AND valid_from <= now() "
            "  AND (valid_until IS NULL OR valid_until > now()) "
            "LIMIT 1"
        ),
        {"ean": ean},
    ).first()
    if not row:
        return None
    return {
        "id": row.id,
        "provider": row.provider,
        "external_id": row.external_id,
        "product_ean": row.product_ean,
        "brand_id": row.brand_id,
        "cashback_rate": row.cashback_rate,  # NUMERIC rate — stays Decimal
    }


def get_pending_store_receipt_scans(db: Session, store_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return accepted scan rows of receipts still in ``store_status='pending'``
    for a store — one row per (receipt, scan) with the receipt's owner.

    Used by the retroactive-cashback flow when a user_suggested store flips
    to ``validation_status='confirmed'``. Only scans with a non-NULL
    ``product_ean`` are returned (offer detection needs an EAN).
    """
    rows = (
        db.execute(
            text(
                "SELECT r.id AS receipt_id, r.user_id, "
                "       s.id AS scan_id, s.product_ean, s.price "
                "FROM receipts r "
                "JOIN scans s ON s.receipt_id = r.id AND s.status = 'accepted' "
                "WHERE r.store_id = :sid "
                "  AND r.store_status = 'pending' "
                "  AND s.product_ean IS NOT NULL "
                "ORDER BY r.id"
            ),
            {"sid": store_id},
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def get_pending_store_receipt_ids(db: Session, store_id: uuid.UUID) -> list[uuid.UUID]:
    """Return every receipt id still in ``store_status='pending'`` for a store.

    Broader than :func:`get_pending_store_receipt_scans` — includes receipts
    with no accepted/offer-eligible scans, so the caller can still flip them
    to ``confirmed``.
    """
    rows = (
        db.execute(
            text("SELECT id FROM receipts WHERE store_id = :sid AND store_status = 'pending'"),
            {"sid": store_id},
        )
        .scalars()
        .all()
    )
    return list(rows)


def confirm_store_receipts(db: Session, receipt_ids: list[uuid.UUID]) -> None:
    """Flip the given receipts to ``store_status='confirmed'``. No-op on []."""
    if not receipt_ids:
        return
    db.execute(
        text("UPDATE receipts SET store_status = 'confirmed' WHERE id = ANY(:ids)"),
        {"ids": receipt_ids},
    )


def has_cashback_for_scan(db: Session, scan_id: uuid.UUID, ean: str) -> bool:
    """Return True if a CREDIT already exists for (scan_id, ean) — idempotence check."""
    row = db.execute(
        text(
            "SELECT 1 FROM cashback_transactions "
            "WHERE scan_id = :scan_id AND product_ean = :ean AND type = 'CREDIT' "
            "LIMIT 1"
        ),
        {"scan_id": scan_id, "ean": ean},
    ).first()
    return row is not None


def insert_cashback_credit(
    db: Session,
    *,
    user_id: uuid.UUID,
    offer_id: uuid.UUID,
    product_ean: str,
    amount: int,
    scan_id: uuid.UUID,
    distributed_at: datetime | None,
) -> uuid.UUID:
    """Insert a CREDIT cashback_transaction. Returns the new transaction ID."""
    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied, distributed_at, scan_id) "
            "VALUES (:id, :uid, 'CREDIT', :amount, 'pending', :ean, :offer_id, "
            "        false, :distributed_at, :scan_id)"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "amount": amount,
            "ean": product_ean,
            "offer_id": offer_id,
            "distributed_at": distributed_at,
            "scan_id": scan_id,
        },
    )
    return tx_id


def credit_cashback_balance(db: Session, user_id: uuid.UUID, amount: int) -> None:
    """
    Atomically add `amount` centimes to user_cashback_balance.

    UPSERT safety net: if the balance row doesn't exist (user registered before
    the cashback fix was deployed), it is created on first credit.
    """
    db.execute(
        text(
            "INSERT INTO user_cashback_balance (user_id, balance, updated_at) "
            "VALUES (:uid, :amount, now()) "
            "ON CONFLICT (user_id) DO UPDATE "
            "SET balance = user_cashback_balance.balance + :amount, updated_at = now()"
        ),
        {"uid": user_id, "amount": amount},
    )


def debit_cashback_balance(db: Session, user_id: uuid.UUID, amount: int) -> None:
    """
    Atomically subtract `amount` centimes from user_cashback_balance.

    Raises InsufficientCashbackBalance if balance < amount (atomic check via WHERE).
    """
    result = db.execute(
        text(
            "UPDATE user_cashback_balance "
            "SET balance = balance - :amount, updated_at = now() "
            "WHERE user_id = :uid AND balance >= :amount"
        ),
        {"uid": user_id, "amount": amount},
    )
    if result.rowcount == 0:
        raise InsufficientCashbackBalance("insufficient cashback balance")


def get_cashback_balance(db: Session, user_id: uuid.UUID) -> int:
    """Return the user's cashback balance in centimes (0 if row missing)."""
    row = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    return row.balance if row else 0


def get_pending_credits(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """
    Return CREDIT transactions with status='pending' for the balance endpoint.

    Refused transactions are excluded — only what the user is still waiting for.
    """
    rows = db.execute(
        text(
            "SELECT id, amount, product_ean, status, boost_applied, created_at "
            "FROM cashback_transactions "
            "WHERE user_id = :uid AND type = 'CREDIT' AND status = 'pending' "
            "ORDER BY created_at DESC"
        ),
        {"uid": user_id},
    ).fetchall()
    return [
        {
            "id": row.id,
            "amount": row.amount,
            "product_ean": row.product_ean,
            "status": row.status,
            "boost_applied": row.boost_applied,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def get_cashback_tx(db: Session, tx_id: uuid.UUID) -> dict[str, Any] | None:
    """Return a single cashback_transaction row by ID, or None."""
    row = db.execute(
        text(
            "SELECT id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "       boost_applied, distributed_at, scan_id, parent_transaction_id, created_at "
            "FROM cashback_transactions WHERE id = :tx_id"
        ),
        {"tx_id": tx_id},
    ).first()
    if not row:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "type": row.type,
        "amount": row.amount,
        "status": row.status,
        "product_ean": row.product_ean,
        "affiliate_offer_id": row.affiliate_offer_id,
        "boost_applied": row.boost_applied,
        "distributed_at": row.distributed_at,
        "scan_id": row.scan_id,
        "parent_transaction_id": row.parent_transaction_id,
        "created_at": row.created_at,
    }


def get_boost_child(db: Session, parent_tx_id: uuid.UUID) -> dict[str, Any] | None:
    """Return the BOOST child transaction of a CREDIT, or None."""
    row = db.execute(
        text(
            "SELECT id, user_id, amount, status "
            "FROM cashback_transactions "
            "WHERE parent_transaction_id = :parent_id AND type = 'BOOST' "
            "LIMIT 1"
        ),
        {"parent_id": parent_tx_id},
    ).first()
    if not row:
        return None
    return {"id": row.id, "user_id": row.user_id, "amount": row.amount, "status": row.status}


def insert_cashback_boost(
    db: Session,
    *,
    user_id: uuid.UUID,
    parent_tx_id: uuid.UUID,
    offer_id: uuid.UUID | None,
    product_ean: str | None,
    amount: int,
) -> uuid.UUID:
    """Insert a BOOST cashback_transaction (immediately distributed). Returns the new tx ID."""
    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied, distributed_at, parent_transaction_id, parent_type) "
            "VALUES (:id, :uid, 'BOOST', :amount, 'pending', :ean, :offer_id, "
            "        false, now(), :parent_id, 'boost_parent')"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "amount": amount,
            "ean": product_ean,
            "offer_id": offer_id,
            "parent_id": parent_tx_id,
        },
    )
    return tx_id


def mark_boost_applied(db: Session, credit_tx_id: uuid.UUID) -> bool:
    """Atomically claim the boost for a CREDIT transaction.

    Flips ``boost_applied`` false → true with the ``boost_applied = false``
    guard baked into the WHERE clause, so two concurrent boosters race on
    a single UPDATE : exactly one matches a row. Returns True for the
    winner, False if the flag was already set (the caller must then abort
    without debiting CAB or inserting a BOOST row — audit RW-money F-4).
    """
    result = db.execute(
        text("UPDATE cashback_transactions SET boost_applied = true WHERE id = :tx_id AND boost_applied = false"),
        {"tx_id": credit_tx_id},
    )
    return result.rowcount == 1


def update_cashback_tx_status(db: Session, tx_id: uuid.UUID, new_status: str) -> None:
    """Update the status field of a cashback_transaction."""
    db.execute(
        text("UPDATE cashback_transactions SET status = :status WHERE id = :tx_id"),
        {"status": new_status, "tx_id": tx_id},
    )


def update_cashback_tx_distributed(db: Session, tx_id: uuid.UUID) -> None:
    """Set distributed_at = now() on a cashback_transaction."""
    db.execute(
        text("UPDATE cashback_transactions SET distributed_at = now() WHERE id = :tx_id"),
        {"tx_id": tx_id},
    )


def is_user_subscriber(db: Session, user_id: uuid.UUID) -> bool:
    """Return True if the user has an active subscription."""
    row = db.execute(
        text("SELECT 1 FROM subscriptions WHERE user_id = :uid AND status = 'active' AND expires_at > now() LIMIT 1"),
        {"uid": user_id},
    ).first()
    return row is not None


def create_affiliate_offer(
    db: Session,
    *,
    provider: str,
    external_id: str,
    product_ean: str,
    brand_id: uuid.UUID,
    cashback_rate,  # NUMERIC rate — kept as Decimal/float from caller
    valid_from: datetime,
    valid_until: datetime | None,
) -> uuid.UUID:
    """Insert a new affiliate offer. Returns the new offer ID."""
    offer_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO affiliate_offers "
            "    (id, provider, external_id, product_ean, brand_id, cashback_rate, valid_from, valid_until) "
            "VALUES (:id, :provider, :ext_id, :ean, :bid, :rate, :valid_from, :valid_until)"
        ),
        {
            "id": offer_id,
            "provider": provider,
            "ext_id": external_id,
            "ean": product_ean,
            "bid": brand_id,
            "rate": cashback_rate,
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
    )
    return offer_id


def insert_cashback_withdrawal_tx(db: Session, user_id: uuid.UUID, amount: int) -> uuid.UUID:
    """
    Insert a WITHDRAWAL cashback_transaction — status='confirmed', distributed_at=now().

    The accounting debit is an immediate fact; cashback_withdrawals tracks the
    operational payment status separately.
    Returns the new transaction ID.
    """
    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, distributed_at, boost_applied) "
            "VALUES (:id, :uid, 'WITHDRAWAL', :amount, 'confirmed', now(), false)"
        ),
        {"id": tx_id, "uid": user_id, "amount": amount},
    )
    return tx_id


def set_withdrawal_provider_ref(
    db: Session,
    withdrawal_id: uuid.UUID,
    payment_provider_ref: str,
) -> None:
    """
    Store the payment provider reference after a successful payout initiation.

    Sets both payment_provider_ref and provider_initiated_at atomically
    (required by the provider_coherence CHECK constraint on cashback_withdrawals).

    Raises WithdrawalNotFound if the withdrawal row does not exist.
    """
    result = db.execute(
        text(
            "UPDATE cashback_withdrawals SET payment_provider_ref = :ref, provider_initiated_at = now() WHERE id = :wid"
        ),
        {"ref": payment_provider_ref, "wid": withdrawal_id},
    )
    if result.rowcount == 0:
        raise WithdrawalNotFound(f"cashback_withdrawals {withdrawal_id} not found")


def insert_cashback_withdrawal(
    db: Session,
    user_id: uuid.UUID,
    cashback_transaction_id: uuid.UUID,
    amount: int,
) -> uuid.UUID:
    """
    Insert a cashback_withdrawals row — status='pending'.

    payment_provider_ref and processed_at are set later (by the route after the
    provider call, or by ratis_batch_reconciliation on retry).
    Returns the new withdrawal ID.
    """
    withdrawal_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_withdrawals "
            "    (id, user_id, cashback_transaction_id, amount, status) "
            "VALUES (:id, :uid, :tx_id, :amount, 'pending')"
        ),
        {
            "id": withdrawal_id,
            "uid": user_id,
            "tx_id": cashback_transaction_id,
            "amount": amount,
        },
    )
    return withdrawal_id


def get_all_affiliate_offers(db: Session) -> list[dict[str, Any]]:
    """Return all affiliate offers for admin listing."""
    rows = db.execute(
        text(
            "SELECT id, provider, external_id, product_ean, brand_id, cashback_rate, "
            "       valid_from, valid_until, created_at "
            "FROM affiliate_offers ORDER BY created_at DESC"
        )
    ).fetchall()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "external_id": row.external_id,
            "product_ean": row.product_ean,
            "brand_id": row.brand_id,
            "cashback_rate": row.cashback_rate,
            "valid_from": row.valid_from,
            "valid_until": row.valid_until,
            "created_at": row.created_at,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Admin — cashback withdrawals queue (PR1 admin RW)
# ---------------------------------------------------------------------------
def list_withdrawals(
    db: Session,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List cashback_withdrawals rows with optional status filter + pagination.

    Returns ``(rows, total)`` where ``total`` is the count *after* the status
    filter but *before* limit/offset (so the UI can render "page 1 of N").
    Ordering : newest first by ``requested_at`` then ``id`` for stability.
    """
    # Two fully-literal queries — choose by whether ``status`` is filtered.
    # Avoids any string concatenation of SQL fragments (S608-clean) ; the
    # only "variation" is the presence of a WHERE clause, which is always
    # an entirely static literal.
    if status is None:
        count_sql = "SELECT COUNT(*) FROM cashback_withdrawals"
        rows_sql = (
            "SELECT id, user_id, amount, status, payment_provider_ref, "
            "       provider_initiated_at, requested_at, processed_at, "
            "       failure_reason, last_reconciled_at "
            "FROM cashback_withdrawals "
            "ORDER BY requested_at DESC, id DESC "
            "LIMIT :limit OFFSET :offset"
        )
        params: dict[str, Any] = {"limit": limit, "offset": offset}
    else:
        count_sql = "SELECT COUNT(*) FROM cashback_withdrawals WHERE status = :status"
        rows_sql = (
            "SELECT id, user_id, amount, status, payment_provider_ref, "
            "       provider_initiated_at, requested_at, processed_at, "
            "       failure_reason, last_reconciled_at "
            "FROM cashback_withdrawals WHERE status = :status "
            "ORDER BY requested_at DESC, id DESC "
            "LIMIT :limit OFFSET :offset"
        )
        params = {"limit": limit, "offset": offset, "status": status}

    total = db.execute(
        text(count_sql),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    ).scalar_one()

    rows = db.execute(text(rows_sql), params).fetchall()

    return (
        [
            {
                "id": r.id,
                "user_id": r.user_id,
                "amount": r.amount,
                "status": r.status,
                "payment_provider_ref": r.payment_provider_ref,
                "provider_initiated_at": r.provider_initiated_at,
                "requested_at": r.requested_at,
                "processed_at": r.processed_at,
                "failure_reason": r.failure_reason,
                "last_reconciled_at": r.last_reconciled_at,
            }
            for r in rows
        ],
        int(total),
    )


def get_withdrawal_for_update(db: Session, withdrawal_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a withdrawal row with ``SELECT ... FOR UPDATE`` (row lock).

    Used by validate / refuse flows so two concurrent admin actions on the
    same row serialize. Returns ``None`` if the row does not exist.
    """
    row = db.execute(
        text("SELECT id, user_id, amount, status FROM cashback_withdrawals WHERE id = :id FOR UPDATE"),
        {"id": withdrawal_id},
    ).first()
    if row is None:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "amount": row.amount,
        "status": row.status,
    }


def mark_withdrawal_processed(
    db: Session,
    withdrawal_id: uuid.UUID,
    payment_provider_ref: str,
) -> None:
    """Set status='processed' + processed_at + provider_ref + initiated_at.

    All fields set in a single UPDATE to satisfy ``processed_check`` and
    ``provider_coherence`` CHECK constraints atomically.
    Raises :class:`WithdrawalNotFound` if the row vanished between the lock
    and this call (defensive — should not happen under FOR UPDATE).
    """
    result = db.execute(
        text(
            "UPDATE cashback_withdrawals "
            "SET status = 'processed', "
            "    processed_at = now(), "
            "    payment_provider_ref = :ref, "
            "    provider_initiated_at = now() "
            "WHERE id = :id"
        ),
        {"id": withdrawal_id, "ref": payment_provider_ref},
    )
    if result.rowcount == 0:
        raise WithdrawalNotFound(f"cashback_withdrawals {withdrawal_id} not found")


def mark_withdrawal_failed(
    db: Session,
    withdrawal_id: uuid.UUID,
    failure_reason: str,
) -> None:
    """Set status='failed' + failure_reason atomically (failure_check)."""
    result = db.execute(
        text("UPDATE cashback_withdrawals SET status = 'failed', failure_reason = :reason WHERE id = :id"),
        {"id": withdrawal_id, "reason": failure_reason},
    )
    if result.rowcount == 0:
        raise WithdrawalNotFound(f"cashback_withdrawals {withdrawal_id} not found")
