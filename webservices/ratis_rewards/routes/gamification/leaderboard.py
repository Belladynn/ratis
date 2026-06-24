"""
GET /gamification/leaderboard/burst-monthly  — top Burst XP, current month
GET /gamification/leaderboard/burst-alltime  — top Burst XP, all time

Replaces the legacy ``GET /gamification/missions/stonks-leaderboard``
endpoint (= dropped 2026-05-09 with the Stonks → Buffer/Burst refonte).

Both endpoints :
* require user JWT
* return ``top_50`` (or ``limit`` query param, capped at 200)
* include the caller's rank + max_xp for the period
"""

from __future__ import annotations

from datetime import UTC, datetime

from deps import get_current_user
from fastapi import APIRouter, Depends, Query
from ratis_core.database import get_db
from ratis_core.models.user import User
from services.leaderboard_service import (
    DEFAULT_LEADERBOARD_TOP_SIZE,
    MAX_LEADERBOARD_LIMIT,
    get_burst_alltime_top,
    get_burst_monthly_top,
    get_user_rank,
)
from sqlalchemy.orm import Session

router = APIRouter()


def _serialize_row(r: dict) -> dict:
    """Compact serializer — UUID and datetime → strings for JSON."""
    return {
        "user_id": str(r["user_id"]),
        "display_name": r["display_name"],
        "xp_earned": r["xp_earned"],
        "burst_count": r["burst_count"],
        "buffer_count": r["buffer_count"],
        "mission_action_type": r["mission_action_type"],
        "mission_qualifier": r["mission_qualifier"],
        "recorded_at": (r["recorded_at"].isoformat().replace("+00:00", "Z") if r["recorded_at"] else None),
    }


@router.get("/gamification/leaderboard/burst-monthly")
def burst_monthly_leaderboard(
    month: str | None = Query(
        default=None,
        description="YYYY-MM (default: current month UTC)",
    ),
    limit: int = Query(
        default=DEFAULT_LEADERBOARD_TOP_SIZE,
        ge=1,
        le=MAX_LEADERBOARD_LIMIT,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return monthly Burst leaderboard + the caller's rank for the month."""
    if not month:
        month = datetime.now(UTC).strftime("%Y-%m")
    top = get_burst_monthly_top(db, month, limit=limit)
    user_stat = get_user_rank(db, current_user.id, period=month)
    return {
        "month": month,
        "top": [_serialize_row(r) for r in top],
        "your_rank": user_stat["rank"],
        "your_max_xp": user_stat["max_xp"],
    }


@router.get("/gamification/leaderboard/burst-alltime")
def burst_alltime_leaderboard(
    limit: int = Query(
        default=DEFAULT_LEADERBOARD_TOP_SIZE,
        ge=1,
        le=MAX_LEADERBOARD_LIMIT,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return all-time Burst leaderboard + the caller's all-time rank."""
    top = get_burst_alltime_top(db, limit=limit)
    user_stat = get_user_rank(db, current_user.id, period=None)
    return {
        "top": [_serialize_row(r) for r in top],
        "your_rank": user_stat["rank"],
        "your_max_xp": user_stat["max_xp"],
    }
