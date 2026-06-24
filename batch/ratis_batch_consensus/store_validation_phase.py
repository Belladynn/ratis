"""
Store validation — Phase 3 of ratis_batch_consensus.

Run after Phases 1+2 (price_consensus trust_score recalc). Looks at every
store with ``validation_status='pending'`` and decides:

  - **Sub-phase 3.1** : if the store has accumulated ≥
    ``min_distinct_eans_for_validation`` distinct EAN with consensus
    ``trust_score ≥ consensus_min_trust_score`` → flip to ``confirmed``,
    write an audit row in ``store_validation_history``, and call rewards
    to credit cashback retroactively on every receipt previously held back
    on this store (``POST /rewards/cashback/process-retroactive``).

  - **Sub-phase 3.2** : if the store is older than
    ``suspicious_after_months`` AND the same EAN-with-trust query returns
    fewer than ``suspicious_threshold_eans`` → flip to ``suspicious``,
    write an audit row with ``meta`` capturing ``distinct_eans_count`` and
    ``age_days``.

Transactional discipline:
  - Each store flip is committed independently (its own session).
  - A failure on one store does not affect others.
  - A failure of the rewards retroactive call is logged but does not
    rollback the flip — the store is confirmed regardless; the cashback
    can be re-triggered manually if needed (and the next batch run will
    not re-flip — idempotent).

Settings consumed (from ``ratis_settings.json`` § ``store_validation``):
  ``min_distinct_eans_for_validation``, ``consensus_min_trust_score``,
  ``suspicious_after_months``, ``suspicious_threshold_eans``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from ratis_core.rewards_client import process_retroactive_cashback
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

log = logging.getLogger("consensus.store_validation")

REQUIRED_KEYS = frozenset(
    {
        "min_distinct_eans_for_validation",
        "consensus_min_trust_score",
        "suspicious_after_months",
        "suspicious_threshold_eans",
    }
)


def _count_distinct_high_trust_eans(db, store_id: uuid.UUID, min_trust: int) -> int:
    """Count distinct product_ean for a store with trust_score ≥ min_trust.

    Filters out NULL trust_score (rows that have never been recalculated yet —
    safer to ignore them than treat them as 0 or as ≥ threshold).
    """
    return int(
        db.execute(
            text(
                """
                SELECT COUNT(DISTINCT product_ean)
                FROM price_consensus
                WHERE store_id = :sid
                  AND trust_score IS NOT NULL
                  AND trust_score >= :min_trust
                """
            ),
            {"sid": str(store_id), "min_trust": min_trust},
        ).scalar()
        or 0
    )


def _flip_to_confirmed(
    session_factory: sessionmaker,
    store_id: uuid.UUID,
    distinct_eans: int,
) -> bool:
    """Open a fresh session, flip the store, write the audit row, commit.

    Returns True on success. Re-raises on DB error so the caller can surface
    it in the batch error list. The caller handles cashback separately.
    """
    with session_factory() as db:
        db.execute(
            text("UPDATE stores SET validation_status = 'confirmed' WHERE id = :sid AND validation_status = 'pending'"),
            {"sid": str(store_id)},
        )
        db.execute(
            text(
                """
                INSERT INTO store_validation_history (
                    id, store_id, from_status, to_status, reason,
                    triggered_by, meta
                ) VALUES (
                    gen_random_uuid(), :sid, 'pending', 'confirmed',
                    'consensus_threshold_reached',
                    'batch:ratis_batch_consensus:store_validation_phase',
                    cast(:meta AS jsonb)
                )
                """
            ),
            {
                "sid": str(store_id),
                "meta": _json({"distinct_eans_count": distinct_eans}),
            },
        )
        db.commit()
    return True


def _flip_to_suspicious(
    session_factory: sessionmaker,
    store_id: uuid.UUID,
    distinct_eans: int,
    age_days: int,
) -> bool:
    """Open a fresh session, flip to suspicious, write audit, commit."""
    with session_factory() as db:
        db.execute(
            text(
                "UPDATE stores SET validation_status = 'suspicious' WHERE id = :sid AND validation_status = 'pending'"
            ),
            {"sid": str(store_id)},
        )
        db.execute(
            text(
                """
                INSERT INTO store_validation_history (
                    id, store_id, from_status, to_status, reason,
                    triggered_by, meta
                ) VALUES (
                    gen_random_uuid(), :sid, 'pending', 'suspicious',
                    'suspicious_timeout',
                    'batch:ratis_batch_consensus:store_validation_phase',
                    cast(:meta AS jsonb)
                )
                """
            ),
            {
                "sid": str(store_id),
                "meta": _json(
                    {
                        "distinct_eans_count": distinct_eans,
                        "age_days": age_days,
                    }
                ),
            },
        )
        db.commit()
    return True


def _json(payload: dict) -> str:
    """Local helper — keep import surface small."""
    import json

    return json.dumps(payload)


def run_store_validation_phase(session_factory: sessionmaker, settings: dict) -> dict[str, int]:
    """Phase 3 entry point.

    Args:
      session_factory: same sessionmaker used by Phase 1+2. Each store flip
                       opens a fresh short-lived session via this factory.
      settings: full settings dict — ``settings['store_validation']`` is
                consumed. Missing keys raise KeyError (fail-fast).

    Returns: stats dict with keys
      ``flipped_confirmed``, ``flipped_suspicious``, ``retroactive_cashback_calls``,
      ``errors``.
    """
    sv = settings["store_validation"]
    missing = REQUIRED_KEYS - sv.keys()
    if missing:
        raise KeyError(f"store_validation settings missing keys: {sorted(missing)}")

    min_trust = int(sv["consensus_min_trust_score"])
    min_eans_confirm = int(sv["min_distinct_eans_for_validation"])
    suspicious_months = int(sv["suspicious_after_months"])
    suspicious_threshold = int(sv["suspicious_threshold_eans"])

    stats: dict[str, int] = {
        "flipped_confirmed": 0,
        "flipped_suspicious": 0,
        "retroactive_cashback_calls": 0,
        "errors": 0,
    }

    # ── Sub-phase 3.1 — pending → confirmed ──────────────────────────────────
    with session_factory() as db:
        pending_ids = [
            row[0]
            for row in db.execute(
                text("SELECT id FROM stores WHERE validation_status = 'pending' ORDER BY created_at")
            ).all()
        ]
        log.info("Phase 3.1: %d pending stores to evaluate", len(pending_ids))

    for store_id in pending_ids:
        try:
            with session_factory() as db:
                distinct_eans = _count_distinct_high_trust_eans(db, store_id, min_trust)
        except Exception:
            log.exception("Phase 3.1: count query failed for store %s", store_id)
            stats["errors"] += 1
            continue

        if distinct_eans < min_eans_confirm:
            continue

        try:
            _flip_to_confirmed(session_factory, store_id, distinct_eans)
            stats["flipped_confirmed"] += 1
            log.info(
                "Phase 3.1: store %s flipped to confirmed (%d EAN ≥ trust %d)",
                store_id,
                distinct_eans,
                min_trust,
            )
        except Exception:
            log.exception("Phase 3.1: flip-to-confirmed failed for store %s", store_id)
            stats["errors"] += 1
            continue

        # Cashback retroactive call — best-effort. Failure is logged but
        # does NOT undo the flip and does NOT abort other stores.
        try:
            process_retroactive_cashback(store_id)
            stats["retroactive_cashback_calls"] += 1
        except Exception as exc:
            log.error(
                "Phase 3.1: retroactive cashback call failed for store %s: %s",
                store_id,
                exc,
            )

    # ── Sub-phase 3.2 — old pending → suspicious ─────────────────────────────
    threshold_date = datetime.now(UTC) - timedelta(days=suspicious_months * 30)

    with session_factory() as db:
        old_pending = db.execute(
            text(
                """
                SELECT id, created_at FROM stores
                WHERE validation_status = 'pending'
                  AND created_at < :cutoff
                ORDER BY created_at
                """
            ),
            {"cutoff": threshold_date},
        ).all()
    log.info("Phase 3.2: %d old pending stores to evaluate", len(old_pending))

    now = datetime.now(UTC)
    for store_id, created_at in old_pending:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        try:
            with session_factory() as db:
                distinct_eans = _count_distinct_high_trust_eans(db, store_id, min_trust)
        except Exception:
            log.exception("Phase 3.2: count query failed for store %s", store_id)
            stats["errors"] += 1
            continue

        if distinct_eans >= suspicious_threshold:
            continue

        age_days = (now - created_at).days
        try:
            _flip_to_suspicious(session_factory, store_id, distinct_eans, age_days)
            stats["flipped_suspicious"] += 1
            log.info(
                "Phase 3.2: store %s flipped to suspicious (%d EAN, %d days old)",
                store_id,
                distinct_eans,
                age_days,
            )
        except Exception:
            log.exception("Phase 3.2: flip-to-suspicious failed for store %s", store_id)
            stats["errors"] += 1

    log.info(
        "Phase 3 done: %d confirmed, %d suspicious, %d retroactive cashback calls, %d errors",
        stats["flipped_confirmed"],
        stats["flipped_suspicious"],
        stats["retroactive_cashback_calls"],
        stats["errors"],
    )
    return stats
