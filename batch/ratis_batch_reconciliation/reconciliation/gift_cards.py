# batch/ratis_batch_reconciliation/reconciliation/gift_cards.py
"""
Réconciliation gift cards — SQL direct (pas d'appel HTTP à ratis_rewards).

⚠️  SYNC OBLIGATOIRE : toute évolution de award_cab dans
webservices/ratis_rewards/repositories/cab_repository.py doit être répercutée ici
pour la partie refund. Voir commentaire ⚠️ RECONCILIATION SYNC dans cab_repository.py.

Limitations acceptées (intentionnelles — c'est un refund, pas une récompense) :
- Multiplicateur de streak NON appliqué (refund = montant exact débité)
- Progression battlepass NON mise à jour (un refund ne compte pas)
- Notifications outbox NON enqueued
"""

from __future__ import annotations

import logging
import uuid

import sentry_sdk
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def reconcile_pending_gift_card_orders(db: Session, dry_run: bool = False) -> int:
    """Refund CAB for boutique gift-card orders stuck 'pending' > 24h.

    A 'shop_purchase' order still 'pending' 24h after creation means Runa
    never resolved it (or the live _mark_failed path failed — audit KP-17).
    The user's CAB is debited with nothing in return. This job marks each
    such order 'failed' and refunds the exact debited CAB amount, in one
    transaction per order. Returns the number of orders reconciled.

    Idempotent: the UPDATE is guarded on status='pending', so a second run
    finds nothing.
    """
    rows = db.execute(
        text(
            "SELECT id, user_id, source_ref_id "
            "FROM gift_card_orders "
            "WHERE status = 'pending' "
            "  AND source_type = 'shop_purchase' "
            "  AND created_at < now() - INTERVAL '24 hours' "
            "ORDER BY created_at"
        )
    ).fetchall()

    count = len(rows)
    log.info(
        "reconcile_pending_gift_card_orders: %d stuck shop_purchase order(s) detected",
        count,
    )

    if dry_run or count == 0:
        return count

    reconciled = 0
    for row in rows:
        order_id: uuid.UUID = row.id
        user_id: uuid.UUID = row.user_id
        source_ref_id: str = row.source_ref_id

        try:
            _reconcile_one_order(db, order_id, user_id, source_ref_id)
            reconciled += 1
            log.info(
                "reconcile_pending_gift_card_orders: order %s failed + CAB refunded to user %s",
                order_id,
                user_id,
            )
        except Exception:
            db.rollback()
            log.error(
                "reconcile_pending_gift_card_orders: failed for order %s — rolled back",
                order_id,
                exc_info=True,
            )

    if reconciled > 0:
        sentry_sdk.capture_message(
            f"reconcile_pending_gift_card_orders: {reconciled} stuck order(s) refunded",
            level="warning",
        )

    return reconciled


def _reconcile_one_order(
    db: Session,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    source_ref_id: str,
) -> None:
    """Mark one stuck order as failed and refund its debited CAB amount.

    All writes are atomic within the caller's transaction (commit per order).
    The UPDATE is guarded on status='pending' so a concurrent run that
    already processed this order produces rowcount=0 and we skip it safely.
    """
    # 1. Atomic status transition — guarded on status='pending'
    result = db.execute(
        text("UPDATE gift_card_orders SET status = 'failed', failed_at = now() WHERE id = :oid AND status = 'pending'"),
        {"oid": order_id},
    )
    if result.rowcount == 0:
        # Concurrent run already processed this order — idempotent skip
        log.info(
            "reconcile_pending_gift_card_orders: order %s already processed by concurrent run — skip",
            order_id,
        )
        return

    # 2. Look up the original debit to know how much to refund
    try:
        debit_tx_id = uuid.UUID(source_ref_id)
    except (ValueError, AttributeError):
        log.warning(
            "reconcile_pending_gift_card_orders: source_ref_id %r for order %s "
            "is not a valid UUID — skipping CAB refund",
            source_ref_id,
            order_id,
        )
        db.commit()
        return

    debit_row = db.execute(
        text("SELECT amount, user_id FROM cabecoin_transactions WHERE id = :tid AND direction = 'debit'"),
        {"tid": debit_tx_id},
    ).first()
    if debit_row is None:
        log.warning(
            "reconcile_pending_gift_card_orders: debit transaction %s for order %s "
            "not found — status set to failed, no CAB refund",
            debit_tx_id,
            order_id,
        )
        db.commit()
        return

    amount: int = debit_row.amount
    refund_user_id: uuid.UUID = debit_row.user_id

    # 3. Refund: bump user_cab_balance + insert credit transaction
    # Plain refund — no streak multiplier, no battlepass progress.
    result = db.execute(
        text("UPDATE user_cab_balance SET balance = balance + :amt WHERE user_id = :uid"),
        {"amt": amount, "uid": refund_user_id},
    )
    if result.rowcount == 0:
        raise RuntimeError(f"user_cab_balance row not found for user {refund_user_id} — rolling back")
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "    (id, user_id, direction, amount, reason) "
            "VALUES (:id, :uid, 'credit', :amt, 'gift_card_refund')"
        ),
        {"id": uuid.uuid4(), "uid": refund_user_id, "amt": amount},
    )

    # Release the fiscal-cap reservation (audit H4) — raw-SQL mirror of
    # gift_card_cap_service.release_gift_card_cap. ``user_id`` here is the
    # order owner (gift_card_orders.user_id) — the cap-holder, same person
    # as the debit's ``refund_user_id`` for a shop_purchase order ; the cap
    # belongs to the order owner, matching release_gift_card_cap's contract.
    cap_row = db.execute(
        text("SELECT cap_reserved_cents FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).first()
    if cap_row and cap_row.cap_reserved_cents:
        db.execute(
            text(
                "UPDATE users SET gift_card_redeemed_ytd_cents = "
                "GREATEST(0, gift_card_redeemed_ytd_cents - :amt) WHERE id = :uid"
            ),
            {"amt": int(cap_row.cap_reserved_cents), "uid": user_id},
        )
        db.execute(
            text("UPDATE gift_card_orders SET cap_reserved_cents = 0 WHERE id = :oid"),
            {"oid": order_id},
        )

    db.commit()
