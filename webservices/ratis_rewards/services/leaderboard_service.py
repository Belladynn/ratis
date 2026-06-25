"""Leaderboard service — Burst monthly + all-time top-N queries.

Backed by ``mission_xp_records`` (= dropped-in replacement for the old
``stonks_records``). 1 row per (user, user_mission) — captures the max
XP earned via Burst paliers on that mission.

Spec : ``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Default leaderboard size — mirrors ``ratis_settings.json``
# ``gamification.burst.leaderboard_top_size``. Routes accept an explicit
# ``limit`` query param up to a hard cap (= 200) to bound the response.
DEFAULT_LEADERBOARD_TOP_SIZE = 50
MAX_LEADERBOARD_LIMIT = 200


def get_burst_monthly_top(
    db: Session,
    month: str,
    *,
    limit: int = DEFAULT_LEADERBOARD_TOP_SIZE,
) -> list[dict[str, Any]]:
    """Return the top-N Burst XP records for the given month (YYYY-MM).

    For each user, the row with the highest ``xp_earned`` for the
    month is kept. Ties are broken by ``recorded_at`` (earliest wins).
    """
    limit = max(1, min(limit, MAX_LEADERBOARD_LIMIT))
    rows = db.execute(
        text(
            "WITH ranked AS ( "
            "    SELECT mxr.user_id, mxr.mission_id, mxr.user_mission_id, "
            "           mxr.xp_earned, mxr.burst_count, mxr.buffer_count, "
            "           mxr.recorded_at, "
            "           ROW_NUMBER() OVER ( "
            "               PARTITION BY mxr.user_id "
            "               ORDER BY mxr.xp_earned DESC, mxr.recorded_at ASC "
            "           ) AS rn "
            "    FROM mission_xp_records mxr "
            "    WHERE to_char(mxr.recorded_at, 'YYYY-MM') = :month "
            ") "
            "SELECT ranked.user_id, ranked.mission_id, ranked.user_mission_id, "
            "       ranked.xp_earned::TEXT AS xp_earned_text, "
            "       ranked.burst_count, ranked.buffer_count, "
            "       ranked.recorded_at, "
            "       u.display_name, "
            "       m.action_type AS mission_action_type, "
            "       m.qualifier   AS mission_qualifier "
            "FROM ranked "
            "JOIN users u ON u.id = ranked.user_id "
            "JOIN missions m ON m.id = ranked.mission_id "
            "WHERE ranked.rn = 1 "
            "ORDER BY ranked.xp_earned DESC, ranked.recorded_at ASC "
            "LIMIT :limit"
        ),
        {"month": month, "limit": limit},
    ).fetchall()
    return [
        {
            "user_id": r.user_id,
            "display_name": r.display_name,
            "xp_earned": int(r.xp_earned_text),
            "burst_count": r.burst_count,
            "buffer_count": r.buffer_count,
            "mission_action_type": r.mission_action_type,
            "mission_qualifier": r.mission_qualifier,
            "recorded_at": r.recorded_at,
        }
        for r in rows
    ]


def get_burst_alltime_top(
    db: Session,
    *,
    limit: int = DEFAULT_LEADERBOARD_TOP_SIZE,
) -> list[dict[str, Any]]:
    """Return the all-time top-N Burst XP records (max XP per user)."""
    limit = max(1, min(limit, MAX_LEADERBOARD_LIMIT))
    rows = db.execute(
        text(
            "WITH ranked AS ( "
            "    SELECT mxr.user_id, mxr.mission_id, mxr.user_mission_id, "
            "           mxr.xp_earned, mxr.burst_count, mxr.buffer_count, "
            "           mxr.recorded_at, "
            "           ROW_NUMBER() OVER ( "
            "               PARTITION BY mxr.user_id "
            "               ORDER BY mxr.xp_earned DESC, mxr.recorded_at ASC "
            "           ) AS rn "
            "    FROM mission_xp_records mxr "
            ") "
            "SELECT ranked.user_id, ranked.mission_id, ranked.user_mission_id, "
            "       ranked.xp_earned::TEXT AS xp_earned_text, "
            "       ranked.burst_count, ranked.buffer_count, "
            "       ranked.recorded_at, "
            "       u.display_name, "
            "       m.action_type AS mission_action_type, "
            "       m.qualifier   AS mission_qualifier "
            "FROM ranked "
            "JOIN users u ON u.id = ranked.user_id "
            "JOIN missions m ON m.id = ranked.mission_id "
            "WHERE ranked.rn = 1 "
            "ORDER BY ranked.xp_earned DESC, ranked.recorded_at ASC "
            "LIMIT :limit"
        ),
        {"limit": limit},
    ).fetchall()
    return [
        {
            "user_id": r.user_id,
            "display_name": r.display_name,
            "xp_earned": int(r.xp_earned_text),
            "burst_count": r.burst_count,
            "buffer_count": r.buffer_count,
            "mission_action_type": r.mission_action_type,
            "mission_qualifier": r.mission_qualifier,
            "recorded_at": r.recorded_at,
        }
        for r in rows
    ]


def get_user_rank(
    db: Session,
    user_id: uuid.UUID,
    *,
    period: str | None = None,
) -> dict[str, Any]:
    """Return the user's leaderboard rank + max XP for the given period.

    Args :
        period — 'YYYY-MM' for monthly rank, ``None`` for all-time.

    Returns :
        ``{"rank": int | None, "max_xp": int | None}``. Both ``None``
        if the user has no record for that period.
    """
    if period:
        # Monthly : user's max XP within the month.
        max_row = db.execute(
            text(
                "SELECT MAX(xp_earned)::TEXT AS max_xp "
                "FROM mission_xp_records "
                "WHERE user_id = :uid "
                "AND to_char(recorded_at, 'YYYY-MM') = :month"
            ),
            {"uid": user_id, "month": period},
        ).first()
    else:
        max_row = db.execute(
            text("SELECT MAX(xp_earned)::TEXT AS max_xp FROM mission_xp_records WHERE user_id = :uid"),
            {"uid": user_id},
        ).first()

    if not max_row or max_row.max_xp is None:
        return {"rank": None, "max_xp": None}

    user_max = int(max_row.max_xp)

    if period:
        rank_row = db.execute(
            text(
                "SELECT COUNT(DISTINCT user_id) + 1 AS rank "
                "FROM mission_xp_records "
                "WHERE to_char(recorded_at, 'YYYY-MM') = :month "
                "AND xp_earned > :max_xp"
            ),
            {"month": period, "max_xp": user_max},
        ).first()
    else:
        rank_row = db.execute(
            text("SELECT COUNT(DISTINCT user_id) + 1 AS rank FROM mission_xp_records WHERE xp_earned > :max_xp"),
            {"max_xp": user_max},
        ).first()

    # COUNT(...) + 1 is an aggregate-only SELECT (no GROUP BY) → always exactly
    # one row, so .first() is never None here.
    assert rank_row is not None  # single-row guarantee of the COUNT query
    return {"rank": int(rank_row.rank), "max_xp": user_max}
