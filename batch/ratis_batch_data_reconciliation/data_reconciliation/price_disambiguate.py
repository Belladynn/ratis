"""Job 3 — price_disambiguate (Phase 2 STUB).

Tranches ``parsed_ticket_items`` whose multi-OCR runs produced
diverging ``price_cents`` candidates by checking each candidate against
the live ``price_consensus`` retailer-keyed for the item's EAN.

Depends on :

- ``parsed_ticket_items.status='disambiguation'`` + ``disambiguation_candidates JSONB``
  to be introduced by ``ARCH_receipt_reconciliation.md`` (not yet merged
  at Phase 1 time).

Stub kept so ``run.py`` orchestrates 4 jobs end-to-end today. See
``ARCH_BATCH_DATA_RECONCILIATION.md`` § "Plan d'implémentation par phases"
→ Phase 2.B.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def reconcile_price_disambiguate(db: Session, *, dry_run: bool = False) -> dict:
    """Phase 2 stub — returns zero-counts and a marker flag.

    Real implementation will :
      - SELECT items WHERE status='disambiguation'
      - For each : resolve retailer_id from receipt.store, query
        ``price_consensus(retailer_id, ean)`` → consensus_price_cents
      - If exactly one disambiguation_candidate is within
        ``consensus_tolerance_pct`` (settings) of the consensus → UPDATE
        items.price_cents + status='matched' + emit audit log

    Returns the canonical stat shape so ``run.py`` log stays consistent.
    """
    log.warning(
        "reconcile_price_disambiguate: Job 3 not implemented yet (Phase 2) — skipping, dry_run=%s",
        dry_run,
    )
    return {
        "count_processed": 0,
        "count_resolved": 0,
        "count_skipped": 0,
        "stub_phase_2": True,
    }


__all__ = ["reconcile_price_disambiguate"]
