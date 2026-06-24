# batch/ratis_batch_reconciliation/reconciliation/cab.py
"""
Réconciliation CAB — SQL direct (pas d'appel HTTP à ratis_rewards).

⚠️  SYNC OBLIGATOIRE : toute évolution de award_cab dans
webservices/ratis_rewards/repositories/cab_repository.py doit être répercutée ici.
Voir commentaire ⚠️ RECONCILIATION SYNC dans cab_repository.py.

Limitations acceptées (documentées dans ARCH.md) :
- Multiplicateur de streak non appliqué sur les transactions réconciliées
- Progression battlepass non mise à jour
- Missions non incrémentées
- Notifications outbox non enqueueées
Ces effets secondaires sont intentionnellement omis — le batch est conservateur.
"""

from __future__ import annotations

import logging
import uuid

from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Mapping scan_type → (cab_reason, settings_key)
_SCAN_TYPE_MAP = {
    "receipt": ("receipt_scan", "cab_per_receipt_scan"),
    "electronic_label": ("label_scan", "cab_per_label_scan"),
    "manual": ("barcode_scan", "cab_per_barcode_scan"),
}


def reconcile_missing_scan_rewards(db: Session, dry_run: bool = False) -> int:
    """
    Detect accepted scans with no corresponding CAB credit and create the missing
    transaction + balance update.

    Returns the number of scans reconciled (or that would be reconciled in dry_run).

    Idempotent: unique partial index uq_cabtx_scan_credit on (reference_id) WHERE
    direction = 'credit' AND reference_type = 'scan' prevents double-insert under
    concurrent runs (write-side guard). A second pass finds no gaps.
    Only processes scans older than 10 minutes (leaves time for the live service path).
    """
    cfg = load_settings().get("rewards", {})

    required_keys = {"cab_per_receipt_scan", "cab_per_label_scan", "cab_per_barcode_scan"}
    missing = required_keys - cfg.keys()
    if missing:
        log.error(
            "reconcile_missing_scan_rewards: missing settings keys %s — aborting",
            missing,
        )
        return 0

    rows = db.execute(
        text("""
        SELECT s.id AS scan_id, s.user_id, s.scan_type
        FROM scans s
        WHERE s.status = 'accepted'
          AND s.scan_type IN ('receipt', 'electronic_label', 'manual')
          AND NOT EXISTS (
              SELECT 1 FROM cabecoin_transactions t
              WHERE t.reference_id = s.id
                AND t.direction = 'credit'
                AND t.reference_type = 'scan'
          )
          AND s.status_updated_at < NOW() - INTERVAL '10 minutes'
        ORDER BY s.status_updated_at
    """)
    ).fetchall()

    count = len(rows)
    log.info("reconcile_missing_scan_rewards: %d scan(s) without CAB credit detected", count)

    if dry_run or count == 0:
        return count

    for row in rows:
        scan_id: uuid.UUID = row.scan_id
        user_id: uuid.UUID = row.user_id
        scan_type: str = row.scan_type

        reason, settings_key = _SCAN_TYPE_MAP[scan_type]
        amount: int = cfg.get(settings_key, 0)

        if amount <= 0:
            log.error(
                "reconcile_missing_scan_rewards: %s=%d for scan %s — skipping",
                settings_key,
                amount,
                scan_id,
            )
            continue

        try:
            credited = _credit_scan(db, scan_id, user_id, amount, reason)
            if credited:
                log.info(
                    "reconcile_missing_scan_rewards: credited %d CABs to user %s for scan %s (%s)",
                    amount,
                    user_id,
                    scan_id,
                    reason,
                )
            else:
                log.info(
                    "reconcile_missing_scan_rewards: dp03_idempotence_skip scan=%s already credited by concurrent run",
                    scan_id,
                )
        except Exception:
            db.rollback()
            log.error(
                "reconcile_missing_scan_rewards: failed for scan %s — rolled back",
                scan_id,
                exc_info=True,
            )

    return count


def _credit_scan(
    db: Session,
    scan_id: uuid.UUID,
    user_id: uuid.UUID,
    amount: int,
    reason: str,
) -> bool:
    """
    Insert a CAB credit transaction for ``scan_id`` and bump ``user_cab_balance``.

    Atomic against concurrent runs (DP-03) :
    - INSERT uses ``ON CONFLICT ... DO NOTHING RETURNING id`` against the partial unique
      index ``uq_cabtx_scan_credit (reference_id) WHERE direction='credit' AND
      reference_type='scan'``.
    - If the INSERT is skipped (concurrent run committed first), ``RETURNING id`` yields
      no row → the balance update is **not** executed, otherwise the materialized balance
      would drift while the transaction line stayed unique.

    Returns True if a new credit was inserted, False if the scan was already credited.
    Raises if the user's balance row is missing (data integrity error — caller decides
    how to surface).
    """
    inserted = db.execute(
        text("""
            INSERT INTO cabecoin_transactions
                (id, user_id, amount, direction, reason, reference_id, reference_type, created_at)
            VALUES (:id, :uid, :amount, 'credit', :reason, :ref_id, 'scan', now())
            ON CONFLICT (reference_id) WHERE direction = 'credit' AND reference_type = 'scan'
            DO NOTHING
            RETURNING id
        """),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "amount": amount,
            "reason": reason,
            "ref_id": scan_id,
        },
    ).scalar()

    if inserted is None:
        # Concurrent run already credited this scan — nothing to commit, nothing to
        # rollback (no error occurred). Just signal "skipped" and let caller continue.
        return False

    result = db.execute(
        text("""
            UPDATE user_cab_balance
            SET balance = balance + :amount
            WHERE user_id = :uid
        """),
        {"amount": amount, "uid": user_id},
    )
    if result.rowcount == 0:
        raise RuntimeError(f"user_cab_balance row not found for user {user_id} — rolling back")
    db.commit()
    return True


def check_cab_balance_integrity(db: Session) -> list[dict]:
    """
    Verify that user_cab_balance.balance equals the sum of cabecoin_transactions per user.

    Returns a list of dicts for users with a drift:
      {"user_id": UUID, "stored_balance": int, "computed_balance": int, "drift": int}

    Never corrects — alerts only. Intervention manuelle requise.
    """
    rows = db.execute(
        text("""
        SELECT ucb.user_id,
               ucb.balance AS stored_balance,
               COALESCE(SUM(
                   CASE WHEN t.direction = 'credit' THEN t.amount ELSE -t.amount END
               ), 0) AS computed_balance
        FROM user_cab_balance ucb
        LEFT JOIN cabecoin_transactions t ON t.user_id = ucb.user_id
        GROUP BY ucb.user_id, ucb.balance
        HAVING ucb.balance != COALESCE(SUM(
            CASE WHEN t.direction = 'credit' THEN t.amount ELSE -t.amount END
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
            "check_cab_balance_integrity: %d user(s) with CAB balance drift — manual intervention required: %s",
            len(drifts),
            [(str(d["user_id"]), d["drift"]) for d in drifts],
        )
    else:
        log.info("check_cab_balance_integrity: no drift detected")

    return drifts
