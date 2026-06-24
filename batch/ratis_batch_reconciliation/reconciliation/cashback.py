# batch/ratis_batch_reconciliation/reconciliation/cashback.py
"""
Réconciliation Cashback — SQL direct.

⚠️  SYNC OBLIGATOIRE : toute évolution de detect_cashback dans
webservices/ratis_rewards/services/cashback_service.py doit être répercutée ici.
Voir commentaire ⚠️ RECONCILIATION SYNC dans cashback_service.py.

Limitations acceptées :
- reconcile_missing_cashback_scans insère tous les CREDITs en status='pending'
  (pas d'avance abonné) — conservateur, le webhook marque distributed_at.
- reconcile_pending_withdrawals : log ERROR + comptage uniquement (stub V1,
  pas de client Stripe disponible pour retry automatique).
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_EXPIRY_DAYS = 90  # cashback_pending_expiry_days


def reconcile_expired_cashbacks(db: Session, dry_run: bool = False) -> int:
    """
    Mark CREDIT cashback_transactions as 'refused' if pending for more than 90 days.

    Returns number of rows affected (or would be affected in dry_run).
    """
    rows = db.execute(
        text("""
        SELECT id, user_id, amount
        FROM cashback_transactions
        WHERE type = 'CREDIT'
          AND status = 'pending'
          AND created_at < NOW() - (CAST(:expiry AS INT) * INTERVAL '1 day')
    """),
        {"expiry": _EXPIRY_DAYS},
    ).fetchall()

    count = len(rows)
    log.info("reconcile_expired_cashbacks: %d expired pending cashback(s) detected", count)

    if dry_run or count == 0:
        return count

    for row in rows:
        try:
            result = db.execute(
                text("""
                UPDATE cashback_transactions
                SET status = 'refused'
                WHERE id = :id AND status = 'pending'
            """),
                {"id": row.id},
            )
            if result.rowcount == 0:
                log.warning(
                    "reconcile_expired_cashbacks: tx %s already changed (concurrent update?) — skipping",
                    row.id,
                )
                db.rollback()
                continue
            db.commit()
            log.info(
                "reconcile_expired_cashbacks: tx %s → refused (user=%s, amount=%d)",
                row.id,
                row.user_id,
                row.amount,
            )
        except Exception:
            db.rollback()
            log.error("reconcile_expired_cashbacks: failed for tx %s", row.id, exc_info=True)

    return count


def reconcile_missing_cashback_scans(db: Session, dry_run: bool = False) -> int:
    """
    Detect receipt scans accepted with an active affiliate offer but no cashback_transaction.
    Insert a CREDIT (status='pending') for each missing line.

    Idempotent: unique partial index uq_cashbacktx_scan_ean_credit on (scan_id, product_ean)
    WHERE type = 'CREDIT' prevents double-insert under concurrent runs (write-side guard).
    Read-side NOT EXISTS in the detection query acts as a fast-path to avoid unnecessary work.
    Amount = price * cashback_rate (same logic as detect_cashback in cashback_service.py).
    All inserted CREDITs are status='pending' — no subscriber advance (conservative).

    Returns number of (scan, ean) pairs actually inserted (not just detected).
    """
    rows = db.execute(
        text("""
        SELECT s.id AS scan_id,
               s.user_id,
               s.product_ean AS ean,
               s.price,
               ao.id AS offer_id,
               ao.cashback_rate
        FROM scans s
        JOIN affiliate_offers ao ON ao.product_ean = s.product_ean
            AND ao.valid_from <= now()
            AND (ao.valid_until IS NULL OR ao.valid_until > now())
        WHERE s.scan_type = 'receipt'
          AND s.status = 'accepted'
          AND s.product_ean IS NOT NULL
          AND s.status_updated_at < NOW() - INTERVAL '10 minutes'
          AND NOT EXISTS (
              SELECT 1 FROM cashback_transactions ct
              WHERE ct.scan_id = s.id
                AND ct.product_ean = s.product_ean
                AND ct.type = 'CREDIT'
          )
        ORDER BY s.status_updated_at
    """)
    ).fetchall()

    count = len(rows)
    log.info("reconcile_missing_cashback_scans: %d missing cashback line(s) detected", count)

    if dry_run or count == 0:
        return count

    inserted = 0
    for row in rows:
        amount = round(Decimal(str(row.cashback_rate)) * row.price)
        if amount <= 0:
            log.warning(
                "reconcile_missing_cashback_scans: zero amount for scan=%s ean=%s rate=%s — skipping",
                row.scan_id,
                row.ean,
                row.cashback_rate,
            )
            continue
        try:
            db.execute(
                text("""
                INSERT INTO cashback_transactions
                    (id, user_id, type, amount, status, scan_id, product_ean,
                     affiliate_offer_id, boost_applied, created_at)
                VALUES (:id, :uid, 'CREDIT', :amount, 'pending', :sid, :ean,
                        :offer_id, false, now())
                ON CONFLICT (scan_id, product_ean) WHERE type = 'CREDIT'
                DO NOTHING
            """),
                {
                    "id": uuid.uuid4(),
                    "uid": row.user_id,
                    "amount": amount,
                    "sid": row.scan_id,
                    "ean": row.ean,
                    "offer_id": row.offer_id,
                },
            )
            db.commit()
            inserted += 1
            log.info(
                "reconcile_missing_cashback_scans: inserted CREDIT %d centimes for user=%s scan=%s ean=%s",
                amount,
                row.user_id,
                row.scan_id,
                row.ean,
            )
        except Exception:
            db.rollback()
            log.error(
                "reconcile_missing_cashback_scans: failed for scan=%s ean=%s",
                row.scan_id,
                row.ean,
                exc_info=True,
            )

    return inserted


def reconcile_pending_withdrawals(db: Session, dry_run: bool = False) -> int:
    """
    Detect cashback_withdrawals stuck in 'pending' for more than 24 hours.

    V1 stub — logs ERROR for human intervention. No automatic retry.
    Full retry via Stripe will be wired when payout_client supports polling.

    Two cases:
    - payment_provider_ref IS NULL → payout was never initiated (crash between commit and Stripe call)
    - payment_provider_ref IS NOT NULL → payout was initiated but no webhook received

    Returns number of stuck withdrawals detected.
    """
    rows = db.execute(
        text("""
        SELECT id, user_id, amount, payment_provider_ref, requested_at
        FROM cashback_withdrawals
        WHERE status = 'pending'
          AND requested_at < NOW() - INTERVAL '24 hours'
        ORDER BY requested_at
    """)
    ).fetchall()

    count = len(rows)

    if count == 0:
        log.info("reconcile_pending_withdrawals: no stuck withdrawals detected")
        return 0

    no_ref = [r for r in rows if r.payment_provider_ref is None]
    has_ref = [r for r in rows if r.payment_provider_ref is not None]

    if no_ref:
        log.error(
            "reconcile_pending_withdrawals: %d withdrawal(s) stuck with NULL payment_provider_ref "
            "(payout never initiated) — manual intervention required: ids=%s",
            len(no_ref),
            [str(r.id) for r in no_ref],
        )

    if has_ref:
        log.error(
            "reconcile_pending_withdrawals: %d withdrawal(s) with payment_provider_ref but no "
            "webhook received — check Stripe dashboard: ids=%s refs=%s",
            len(has_ref),
            [str(r.id) for r in has_ref],
            [r.payment_provider_ref for r in has_ref],
        )

    log.info("reconcile_pending_withdrawals: %d total stuck withdrawal(s) detected", count)
    return count


def check_cashback_balance_integrity(db: Session) -> list[dict]:
    """
    Verify user_cashback_balance.balance equals computed balance from cashback_transactions.

    Computation rules (from ARCH.md):
    - CREDIT/BOOST with distributed_at IS NOT NULL → +amount (credited to balance)
    - WITHDRAWAL → -amount (debited at initiation)
    - CREDIT/BOOST refused with distributed_at IS NOT NULL → absorbed by Ratis, not deducted

    Returns list of dicts for users with drift. Never corrects — alerts only.
    """
    rows = db.execute(
        text("""
        SELECT ucb.user_id,
               ucb.balance AS stored_balance,
               COALESCE(SUM(
                   CASE
                       WHEN ct.type IN ('CREDIT', 'BOOST')
                            AND ct.distributed_at IS NOT NULL THEN ct.amount
                       WHEN ct.type = 'WITHDRAWAL' THEN -ct.amount
                       ELSE 0
                   END
               ), 0) AS computed_balance
        FROM user_cashback_balance ucb
        LEFT JOIN cashback_transactions ct ON ct.user_id = ucb.user_id
        GROUP BY ucb.user_id, ucb.balance
        HAVING ucb.balance != COALESCE(SUM(
            CASE
                WHEN ct.type IN ('CREDIT', 'BOOST')
                     AND ct.distributed_at IS NOT NULL THEN ct.amount
                WHEN ct.type = 'WITHDRAWAL' THEN -ct.amount
                ELSE 0
            END
        ), 0)
    """)
    ).fetchall()

    drifts = [
        {
            "user_id": row.user_id,
            "stored_balance": row.stored_balance,
            "computed_balance": row.computed_balance,
            "drift": row.stored_balance - row.computed_balance,
        }
        for row in rows
    ]

    if drifts:
        log.error(
            "check_cashback_balance_integrity: %d user(s) with cashback balance drift — "
            "manual intervention required: %s",
            len(drifts),
            [(str(d["user_id"]), d["drift"]) for d in drifts],
        )
    else:
        log.info("check_cashback_balance_integrity: no drift detected")

    return drifts
