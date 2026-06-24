"""Admin stats endpoints — RW (PR8).

Read-only aggregations over ``cabecoin_transactions``. ADMIN_API_KEY
suffit (no TOTP) — these endpoints don't mutate state.

Endpoint : ``GET /api/v1/admin/stats/cab``

Query params (all optional) :

- ``from`` (date) : window start, default = today - 30 days (UTC)
- ``to`` (date)   : window end (inclusive end-of-day), default = today (UTC)
- ``group_by``    : 'reason' | 'day' | 'user' — default 'reason'

Window semantics : ``[from 00:00:00, to+1day 00:00:00)`` UTC, so
``from=2026-04-01 to=2026-04-30`` covers the full month.

Response shape :

::

    {
      "from": "YYYY-MM-DD",
      "to":   "YYYY-MM-DD",
      "summary": {
          "total_credit_cents": int,
          "total_debit_cents":  int,
          "net_emission_cents": int,
          "transaction_count":  int,
          "user_count_active":  int,
      },
      "breakdown_by_reason": [...]   # always present (default group_by)
      OR "breakdown_by_day":  [...]
      OR "breakdown_by_user": [...]
      "top_earners": [...],          # top 10, always included
    }
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from services import cab_stats_service
from sqlalchemy.orm import Session

router = APIRouter()


_DEFAULT_WINDOW_DAYS = 30
_TOP_EARNERS_LIMIT = 10


@router.get(
    "/admin/stats/cab",
    dependencies=[Depends(verify_admin_key)],
)
def admin_stats_cab(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    group_by: Literal["reason", "day", "user"] = Query(default="reason"),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated CAB-economy stats for the given window."""
    today = datetime.now(UTC).date()
    effective_to = date_to if date_to is not None else today
    effective_from = date_from if date_from is not None else effective_to - timedelta(days=_DEFAULT_WINDOW_DAYS)

    if effective_from > effective_to:
        raise HTTPException(status_code=422, detail="invalid_date_range")

    summary = cab_stats_service.compute_summary(db, date_from=effective_from, date_to=effective_to)

    body: dict = {
        "from": effective_from.isoformat(),
        "to": effective_to.isoformat(),
        "summary": summary,
    }

    if group_by == "reason":
        body["breakdown_by_reason"] = cab_stats_service.breakdown_by_reason(
            db, date_from=effective_from, date_to=effective_to
        )
    elif group_by == "day":
        body["breakdown_by_day"] = cab_stats_service.breakdown_by_day(
            db, date_from=effective_from, date_to=effective_to
        )
    else:  # group_by == "user"
        body["breakdown_by_user"] = cab_stats_service.breakdown_by_user(
            db,
            date_from=effective_from,
            date_to=effective_to,
            limit=_TOP_EARNERS_LIMIT,
        )

    body["top_earners"] = cab_stats_service.breakdown_by_user(
        db,
        date_from=effective_from,
        date_to=effective_to,
        limit=_TOP_EARNERS_LIMIT,
    )
    return body
