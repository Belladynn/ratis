"""Aggregation service for admin pipeline stats (PR8).

Read-only SQL aggregations over ``scans`` for monitoring the receipt/
label pipeline match rate, top rejection reasons, match-method
distribution, and store-status distribution.

Scope rule : we filter on ``scans.scanned_at`` between ``date_from``
(inclusive) and ``date_to`` (exclusive). The route layer uses
``datetime`` (not ``date``) because pipeline observability is hour-
grained — last 7 days by default — but the service stays generic and
takes whatever bounds the caller supplies.

Status mapping : pipeline emits ``matched`` / ``unresolved`` /
``rejected``. Legacy v2 still produces ``accepted`` / ``unmatched``.
We treat the v3 values as canonical (per ARCH_receipt_pipeline §
Cardinal state) and count v2 ``accepted`` rows as ``matched``-equivalent
so the dashboard surface stays consistent across the migration window.
``failed`` (legacy worker error) is folded into ``rejected``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

# --- status mapping helpers ---------------------------------------------------
# Single source of truth so SQL CASE expressions stay in sync between
# the summary aggregate and individual breakdowns.
_MATCHED = ("matched", "accepted")
_UNRESOLVED = ("unresolved", "unmatched")
_REJECTED = ("rejected", "failed")


def compute_summary(db: Session, *, date_from: datetime, date_to: datetime) -> dict[str, Any]:
    """Aggregate pipeline counters for the window.

    Returns
    -------
    dict with keys :
        - ``scan_count`` : COUNT(*) of all scans
        - ``matched_count`` : status in ('matched', 'accepted')
        - ``unresolved_count`` : status in ('unresolved', 'unmatched')
        - ``rejected_count`` : status in ('rejected', 'failed')
        - ``match_rate_pct`` : matched / (matched + unresolved + rejected) * 100,
          rounded to 1 decimal. ``pending`` rows are excluded from the
          denominator (still being processed). 0.0 when no terminal scans.
    """
    # Use SQLAlchemy expanding bindparams (no f-string SQL → S608-clean).
    stmt = text(
        "SELECT "
        "  COUNT(*) AS scan_count, "
        "  COUNT(*) FILTER (WHERE status IN :matched) AS matched_count, "
        "  COUNT(*) FILTER (WHERE status IN :unresolved) AS unresolved_count, "
        "  COUNT(*) FILTER (WHERE status IN :rejected) AS rejected_count "
        "FROM scans "
        "WHERE scanned_at >= :start AND scanned_at < :end"
    ).bindparams(
        bindparam("matched", expanding=True),
        bindparam("unresolved", expanding=True),
        bindparam("rejected", expanding=True),
    )
    row = db.execute(
        stmt,
        {
            "matched": list(_MATCHED),
            "unresolved": list(_UNRESOLVED),
            "rejected": list(_REJECTED),
            "start": date_from,
            "end": date_to,
        },
    ).first()
    # A GROUP BY-less aggregate (COUNT/COUNT FILTER) always yields exactly
    # one row, so ``.first()`` is never None here.
    assert row is not None
    matched = int(row.matched_count or 0)
    unresolved = int(row.unresolved_count or 0)
    rejected = int(row.rejected_count or 0)
    terminal = matched + unresolved + rejected
    match_rate = round((matched / terminal) * 100, 1) if terminal > 0 else 0.0
    return {
        "scan_count": int(row.scan_count or 0),
        "matched_count": matched,
        "unresolved_count": unresolved,
        "rejected_count": rejected,
        "match_rate_pct": match_rate,
    }


def top_rejected_reasons(
    db: Session, *, date_from: datetime, date_to: datetime, limit: int = 20
) -> list[dict[str, Any]]:
    """Top rejection reasons (status='rejected'/'failed' or 'unresolved').

    Surfaces both rejected AND unresolved reasons because operators care
    about the entire 'didn't match' surface, not just hard-rejects. NULL
    reasons are excluded.
    """
    rows = db.execute(
        text(
            "SELECT rejected_reason AS reason, COUNT(*) AS count "
            "FROM scans "
            "WHERE scanned_at >= :start AND scanned_at < :end "
            "  AND rejected_reason IS NOT NULL "
            "GROUP BY rejected_reason "
            "ORDER BY count DESC, rejected_reason ASC "
            "LIMIT :limit"
        ),
        {"start": date_from, "end": date_to, "limit": limit},
    ).fetchall()
    # ``count`` collides with Row.count() (tuple method) — read the labelled
    # column via the mapping interface so we get the value, not the method.
    return [{"reason": r.reason, "count": int(r._mapping["count"] or 0)} for r in rows]


def by_match_method(db: Session, *, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
    """Distribution of match_method values among matched scans.

    Includes only scans where ``match_method IS NOT NULL`` (matched
    rows). Ordered by count DESC.
    """
    rows = db.execute(
        text(
            "SELECT match_method AS method, COUNT(*) AS count "
            "FROM scans "
            "WHERE scanned_at >= :start AND scanned_at < :end "
            "  AND match_method IS NOT NULL "
            "GROUP BY match_method "
            "ORDER BY count DESC, match_method ASC"
        ),
        {"start": date_from, "end": date_to},
    ).fetchall()
    # ``count`` collides with Row.count() (tuple method) — read the labelled
    # column via the mapping interface so we get the value, not the method.
    return [{"method": r.method, "count": int(r._mapping["count"] or 0)} for r in rows]


def by_store_status(db: Session, *, date_from: datetime, date_to: datetime) -> dict[str, int]:
    """Distribution of ``store_status`` values for the window.

    Returns a flat dict ``{status: count}`` with the keys reported by the
    DB CHECK constraint (``confirmed`` / ``pending`` / ``unknown``).
    Missing keys default to 0 in the output so the dashboard can render
    a stable shape.
    """
    rows = db.execute(
        text(
            "SELECT store_status, COUNT(*) AS count "
            "FROM scans "
            "WHERE scanned_at >= :start AND scanned_at < :end "
            "GROUP BY store_status"
        ),
        {"start": date_from, "end": date_to},
    ).fetchall()
    result = {"confirmed": 0, "pending": 0, "unknown": 0}
    for r in rows:
        if r.store_status is None:
            continue
        # ``count`` collides with Row.count() (tuple method) — read the labelled
        # column via the mapping interface so we get the value, not the method.
        result[r.store_status] = int(r._mapping["count"] or 0)
    return result
