"""Aggregation service for admin CAB stats (PR8).

Read-only SQL aggregations over ``cabecoin_transactions``. The route
layer is a thin wrapper that handles HTTP/auth/Pydantic ; this module
does the GROUP BY work and returns Python primitives.

All queries scope on ``created_at`` between ``date_from`` (inclusive) and
``date_to`` (exclusive of the next day — end-of-day semantics) so a
``from=2026-04-01 to=2026-04-30`` window covers all rows from
2026-04-01 00:00:00 up to but not including 2026-05-01 00:00:00 UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Convert (date_from, date_to) into inclusive-start / exclusive-end UTC datetimes.

    Tests and the route layer should always pass dates ; this helper
    centralizes the "to is end-of-day inclusive" semantics so callers
    don't need to remember to add a day.
    """
    start = datetime.combine(date_from, time.min, tzinfo=UTC)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
    return start, end


def compute_summary(db: Session, *, date_from: date, date_to: date) -> dict[str, int]:
    """Aggregate global counters for the window.

    Returns
    -------
    dict with keys :
        - ``total_credit_cents`` : sum of all ``amount`` where direction='credit'
        - ``total_debit_cents``  : sum of all ``amount`` where direction='debit'
        - ``net_emission_cents`` : credit - debit
        - ``transaction_count``  : COUNT(*)
        - ``user_count_active``  : COUNT(DISTINCT user_id)
    """
    start, end = _bounds(date_from, date_to)
    row = db.execute(
        text(
            "SELECT "
            "  COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) AS total_credit, "
            "  COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0) AS total_debit, "
            "  COUNT(*) AS tx_count, "
            "  COUNT(DISTINCT user_id) AS user_count "
            "FROM cabecoin_transactions "
            "WHERE created_at >= :start AND created_at < :end"
        ),
        {"start": start, "end": end},
    ).first()
    # Aggregate-only SELECT (SUM/COUNT, no GROUP BY) always yields exactly one
    # row — COALESCE/COUNT give 0 on an empty table, never an empty result set.
    assert row is not None  # single-row guarantee of the aggregate query
    credit = int(row.total_credit or 0)
    debit = int(row.total_debit or 0)
    return {
        "total_credit_cents": credit,
        "total_debit_cents": debit,
        "net_emission_cents": credit - debit,
        "transaction_count": int(row.tx_count or 0),
        "user_count_active": int(row.user_count or 0),
    }


def breakdown_by_reason(db: Session, *, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Per-reason aggregation, ordered by total volume DESC.

    Each row : ``{reason, credit_cents, debit_cents, count}``.
    """
    start, end = _bounds(date_from, date_to)
    rows = db.execute(
        text(
            "SELECT reason, "
            "  COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) AS credit_cents, "
            "  COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0) AS debit_cents, "
            "  COUNT(*) AS count "
            "FROM cabecoin_transactions "
            "WHERE created_at >= :start AND created_at < :end "
            "GROUP BY reason "
            "ORDER BY (COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) "
            "        + COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0)) DESC, "
            "         reason ASC"
        ),
        {"start": start, "end": end},
    ).fetchall()
    return [
        {
            "reason": r.reason,
            "credit_cents": int(r.credit_cents or 0),
            "debit_cents": int(r.debit_cents or 0),
            "count": int(r._mapping["count"] or 0),
        }
        for r in rows
    ]


def breakdown_by_day(db: Session, *, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Per-day aggregation (UTC dates), ordered chronologically.

    Each row : ``{day, credit_cents, debit_cents, count}``.
    """
    start, end = _bounds(date_from, date_to)
    rows = db.execute(
        text(
            "SELECT (created_at AT TIME ZONE 'UTC')::date AS day, "
            "  COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) AS credit_cents, "
            "  COALESCE(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END), 0) AS debit_cents, "
            "  COUNT(*) AS count "
            "FROM cabecoin_transactions "
            "WHERE created_at >= :start AND created_at < :end "
            "GROUP BY day "
            "ORDER BY day ASC"
        ),
        {"start": start, "end": end},
    ).fetchall()
    return [
        {
            "day": r.day.isoformat() if r.day else None,
            "credit_cents": int(r.credit_cents or 0),
            "debit_cents": int(r.debit_cents or 0),
            "count": int(r._mapping["count"] or 0),
        }
        for r in rows
    ]


def breakdown_by_user(db: Session, *, date_from: date, date_to: date, limit: int = 10) -> list[dict[str, Any]]:
    """Top earners (highest credit total) for the window.

    Each row : ``{user_id, credit_cents, transaction_count}``.
    Anonymous transactions (``user_id IS NULL``) are excluded.
    """
    start, end = _bounds(date_from, date_to)
    rows = db.execute(
        text(
            "SELECT user_id, "
            "  COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) AS credit_cents, "
            "  COUNT(*) AS transaction_count "
            "FROM cabecoin_transactions "
            "WHERE created_at >= :start AND created_at < :end "
            "  AND user_id IS NOT NULL "
            "GROUP BY user_id "
            "HAVING COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) > 0 "
            "ORDER BY credit_cents DESC, user_id ASC "
            "LIMIT :limit"
        ),
        {"start": start, "end": end, "limit": limit},
    ).fetchall()
    return [
        {
            "user_id": str(r.user_id),
            "credit_cents": int(r.credit_cents or 0),
            "transaction_count": int(r.transaction_count or 0),
        }
        for r in rows
    ]
