"""Job 2 — store_mdd_vote (Phase 2 STUB).

Resolves ambiguous receipts via vote on store-brand (MDD) signals carried
by their parsed items. Depends on :

- ``receipts.status='pending_user_reconciliation'`` introduced by
  ``ARCH_receipt_reconciliation.md`` (not yet merged at Phase 1 time).
- A ``retailer_mdd_brands(retailer_id, brand_name)`` table to seed (also
  not yet existing in main).

This stub is intentionally a no-op so ``run.py`` can call all 4 jobs
sequentially even before Phase 2 lands. End-to-end orchestration is
testable today, swapping the stub for the real implementation later
needs zero change to ``run.py``.

See ``ARCH_BATCH_DATA_RECONCILIATION.md`` § "Plan d'implémentation par
phases" → Phase 2.A.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def reconcile_store_mdd_vote(db: Session, *, dry_run: bool = False) -> dict:
    """Phase 2 stub — returns zero-counts and a marker flag.

    Real implementation will :
      - SELECT receipts WHERE status='pending_user_reconciliation'
      - For each, count items matching MDD brands per retailer candidate
      - If majority pct ≥ ``min_majority_pct`` setting → UPDATE store_id +
        status='matched' + emit pipeline_audit_log

    Returns the canonical stat shape so ``run.py`` log structure stays
    consistent across phases.
    """
    log.warning(
        "reconcile_store_mdd_vote: Job 2 not implemented yet (Phase 2) — skipping, dry_run=%s",
        dry_run,
    )
    return {
        "count_processed": 0,
        "count_resolved": 0,
        "count_skipped": 0,
        "stub_phase_2": True,
    }


__all__ = ["reconcile_store_mdd_vote"]
