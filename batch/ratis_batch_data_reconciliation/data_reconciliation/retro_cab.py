"""Job 4 — retro_cab.

Crédite CAB rétroactif sur les scans nouvellement passés à
``status='matched'`` (typiquement par Job 1 ``ean_recovery`` plus tôt
dans la même run, ou par d'autres mutations entre la précédente run et
celle-ci) puis aggrège par user et déclenche une notif gratitude
``retro_cab_gratitude`` via NT.

Idempotence par construction :

- ``cabecoin_transactions`` UNIQUE partial index
  ``uq_cabtx_retro_scan_credit (reference_id) WHERE direction='credit'
  AND reference_type='retro_scan'`` — un rerun ne peut pas double-créditer.
- Le SELECT initial filtre déjà les scans déjà créditer via NOT EXISTS.
- L'agrégation par user garantit 1 notif max par user par run, même si
  l'user a 50 scans résolus.

⚠️ SYNC OBLIGATOIRE : le calcul du montant CAB par scan
(``_compute_retro_cab``) doit rester cohérent avec
``webservices/ratis_rewards/routes/rewards/events.py`` (mapping scan_type
→ ``cab_per_*_scan``). Toute évolution du barème doit être répercutée
ici. Voir aussi ARCH_cab_economy.md.

Limitations V1 (cf ARCH § Limitations connues) :

- Pas de retry intra-batch sur la notif (un 5xx NT = user rate sa notif,
  le CAB est crédité quand même).
- Le multiplicateur de streak n'est pas appliqué (conservateur, comme
  pour ``ratis_batch_reconciliation``).
- La progression battlepass / missions n'est pas mise à jour.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict

from ratis_core.notifier_client import notify_user
from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Map scans.scan_type → settings key for the CAB amount.
# Mirrors webservices/ratis_rewards/routes/rewards/events.py (which keys
# by reason ; we key by scan_type for the SELECT path).
_SCAN_TYPE_TO_CAB_SETTING = {
    "receipt": "cab_per_receipt_scan",
    "electronic_label": "cab_per_label_scan",
    "manual": "cab_per_barcode_scan",
}


def _compute_retro_cab(scan_type: str, rewards_settings: dict) -> int:
    """Return the CAB amount to credit for a retro-resolved scan, in CAB units.

    Returns 0 (skipped) when the scan_type is unknown — defensive against
    DB drift introducing a new scan_type that the batch doesn't yet know.
    """
    key = _SCAN_TYPE_TO_CAB_SETTING.get(scan_type)
    if key is None:
        return 0
    amount = int(rewards_settings.get(key, 0))
    return max(0, amount)


def reconcile_retro_cab(db: Session, *, dry_run: bool = False) -> dict:
    """Credit CAB retroactively + send aggregated gratitude notif per user.

    Returns canonical counters :
    ``count_users_notified``, ``count_cab_credited``, ``count_skipped``,
    ``count_errors``, ``duration_ms``. ``count_cab_credited`` is the
    *total CABs* (sum across users), not a row count.

    Skip-clean (returns ``{"error": "missing_env", ...}``) if
    ``NOTIFIER_URL`` or ``INTERNAL_API_KEY`` are missing — the function
    logs an error so the orchestrator's structured log surfaces the
    misconfiguration, but doesn't raise (other jobs in run.py keep
    going).
    """
    start = time.monotonic()
    notifier_url = os.environ.get("NOTIFIER_URL", "").strip()
    internal_key = os.environ.get("INTERNAL_API_KEY", "").strip()

    if not notifier_url or not internal_key:
        log.error("retro_cab: missing NOTIFIER_URL or INTERNAL_API_KEY — skipping")
        return {
            "error": "missing_env",
            "count_users_notified": 0,
            "count_cab_credited": 0,
            "count_skipped": 0,
            "count_errors": 0,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }

    settings = load_settings()
    job_settings = settings["data_reconciliation"]["retro_cab"]
    rewards_settings = settings["rewards"]
    max_lookback_hours = int(job_settings["max_lookback_hours"])

    # Find newly-matched scans not yet credited via reference_type='retro_scan'.
    # We look back ``max_lookback_hours`` to recover from a missed run, but
    # the partial UNIQUE index makes the operation idempotent regardless.
    rows = db.execute(
        text(
            """
            SELECT s.id AS scan_id, s.user_id, s.scan_type
            FROM scans s
            WHERE s.status = 'matched'
              AND s.user_id IS NOT NULL
              AND s.status_updated_at > now() - make_interval(hours => :hours)
              AND NOT EXISTS (
                  SELECT 1 FROM cabecoin_transactions ct
                  WHERE ct.reference_type = 'retro_scan'
                    AND ct.reference_id = s.id
              )
            ORDER BY s.user_id, s.status_updated_at ASC
            """
        ),
        {"hours": max_lookback_hours},
    ).fetchall()

    stats = {
        "count_users_notified": 0,
        "count_cab_credited": 0,
        "count_skipped": 0,
        "count_errors": 0,
    }

    # Group per user.
    by_user: dict[uuid.UUID, list] = defaultdict(list)
    for r in rows:
        by_user[r.user_id].append(r)

    for user_id, scans in by_user.items():
        try:
            user_total_cab = _credit_user_scans(
                db,
                user_id=user_id,
                scans=scans,
                rewards_settings=rewards_settings,
                dry_run=dry_run,
            )

            if user_total_cab <= 0:
                # All scans had unknown scan_type or zero amount — log
                # and skip the notif for this user (no CAB → nothing to
                # be grateful about).
                stats["count_skipped"] += 1
                continue

            stats["count_cab_credited"] += user_total_cab

            if dry_run:
                # Dry-run still counts the user as "would notify" so the
                # run.py log surfaces the volume.
                stats["count_users_notified"] += 1
                continue

            _notify_user(
                user_id=user_id,
                scans_count=len(scans),
                cab_total=user_total_cab,
            )
            stats["count_users_notified"] += 1
        except Exception as exc:
            db.rollback()
            stats["count_errors"] += 1
            log.error(
                "retro_cab_user_failed user=%s error=%s",
                user_id,
                exc,
                exc_info=True,
            )

    stats["duration_ms"] = int((time.monotonic() - start) * 1000)
    return stats


def _credit_user_scans(
    db: Session,
    *,
    user_id: uuid.UUID,
    scans: list,
    rewards_settings: dict,
    dry_run: bool,
) -> int:
    """Credit each scan idempotently + bump the materialized balance once.

    Returns the total CAB amount credited for this user across all
    scans (0 when nothing was credited — either dry_run or all amounts
    were zero or all rows hit ON CONFLICT).
    """
    total_credited = 0
    for s in scans:
        amount = _compute_retro_cab(s.scan_type, rewards_settings)
        if amount <= 0:
            continue

        if dry_run:
            total_credited += amount
            continue

        inserted = db.execute(
            text(
                """
                INSERT INTO cabecoin_transactions
                    (id, user_id, amount, direction, reason,
                     reference_id, reference_type, created_at)
                VALUES
                    (:id, :uid, :amount, 'credit', 'retro_scan',
                     :ref_id, 'retro_scan', now())
                ON CONFLICT (reference_id) WHERE direction = 'credit'
                                            AND reference_type = 'retro_scan'
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": str(user_id),
                "amount": amount,
                "ref_id": str(s.scan_id),
            },
        ).scalar()
        if inserted is None:
            # Concurrent run already credited this scan — skip silently.
            # ``RETURNING id`` returning NULL is the canonical signal
            # (ratis_batch_reconciliation/reconciliation/cab.py same
            # pattern, DP-03).
            continue
        total_credited += amount

    if total_credited > 0 and not dry_run:
        # One UPDATE per user (atomic R09 pattern adapted — credit only,
        # no balance ≥ amount guard since a credit never goes negative).
        result = db.execute(
            text(
                """
                UPDATE user_cab_balance
                SET balance = balance + :amount
                WHERE user_id = :uid
                """
            ),
            {"amount": total_credited, "uid": str(user_id)},
        )
        if result.rowcount == 0:
            raise RuntimeError(
                f"user_cab_balance row missing for user {user_id} — data integrity error, aborting credit"
            )
        db.commit()

    return total_credited


def _notify_user(
    *,
    user_id: uuid.UUID,
    scans_count: int,
    cab_total: int,
) -> None:
    """Trigger NT — fire-and-forget. Never raises (notify_user swallows).

    Wraps ``ratis_core.notifier_client.notify_user`` for callability
    and ease of monkeypatching in tests. Errors are logged inside
    ``notify_user`` itself ; we only emit a debug log here on success
    intent.
    """
    notify_user(
        user_id=user_id,
        notif_type="retro_cab_gratitude",
        data={"scans_count": scans_count, "cab_total": cab_total},
    )


__all__ = ["reconcile_retro_cab"]
