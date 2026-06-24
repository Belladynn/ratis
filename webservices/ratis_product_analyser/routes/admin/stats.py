"""Admin stats endpoints — PA (PR8).

Read-only aggregations over ``scans`` for the receipt/label pipeline
monitoring dashboard. ADMIN_API_KEY suffit (no TOTP).

Endpoint : ``GET /api/v1/admin/pipeline/stats``

Query params (all optional) :

- ``from`` (datetime) : window start, default = now - 7 days (UTC)
- ``to``   (datetime) : window end (exclusive), default = now (UTC)

Window semantics : ``[from, to)`` UTC. The default window (7 days) is
finer-grained than the RW CAB endpoint because pipeline failures need
hour-level surfacing.

Response shape :

::

    {
      "from": "ISO8601",
      "to":   "ISO8601",
      "summary": {
          "scan_count": int, "matched_count": int,
          "unresolved_count": int, "rejected_count": int,
          "match_rate_pct": float,
      },
      "top_rejected_reasons": [{"reason": str, "count": int}, ...],
      "by_match_method":      [{"method": str, "count": int}, ...],
      "by_store_status":      {"confirmed": int, "pending": int, "unknown": int},
    }

NOTE : pas de latency p50/p99 en v1 — on aurait besoin d'un
``processing_time_ms`` dans ``pipeline_audit_log`` ou ``scans``, ce qui
n'existe pas encore. Follow-up tracké dans DECISIONS_PENDING.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from services import pipeline_stats_service
from sqlalchemy.orm import Session

router = APIRouter()


_DEFAULT_WINDOW_DAYS = 7
_TOP_REJECTED_LIMIT = 20


@router.get(
    "/admin/pipeline/stats",
    dependencies=[Depends(verify_admin_key)],
)
def admin_pipeline_stats(
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated pipeline stats for the given window."""
    now = datetime.now(UTC)
    effective_to = date_to if date_to is not None else now
    effective_from = date_from if date_from is not None else effective_to - timedelta(days=_DEFAULT_WINDOW_DAYS)

    # Normalize naive datetimes to UTC to keep DB comparisons consistent.
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=UTC)
    if effective_to.tzinfo is None:
        effective_to = effective_to.replace(tzinfo=UTC)

    if effective_from > effective_to:
        raise HTTPException(status_code=422, detail="invalid_date_range")

    summary = pipeline_stats_service.compute_summary(db, date_from=effective_from, date_to=effective_to)
    rejected = pipeline_stats_service.top_rejected_reasons(
        db, date_from=effective_from, date_to=effective_to, limit=_TOP_REJECTED_LIMIT
    )
    methods = pipeline_stats_service.by_match_method(db, date_from=effective_from, date_to=effective_to)
    stores = pipeline_stats_service.by_store_status(db, date_from=effective_from, date_to=effective_to)

    return {
        "from": effective_from.isoformat(),
        "to": effective_to.isoformat(),
        "summary": summary,
        "top_rejected_reasons": rejected,
        "by_match_method": methods,
        "by_store_status": stores,
    }
