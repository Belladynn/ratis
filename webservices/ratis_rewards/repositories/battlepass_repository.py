"""
Battlepass repository — raw SQL, all writes within the caller's transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.exceptions import (
    ActiveSeasonConflict,
    MilestoneNumberConflict,
    SeasonNotFound,
    SeasonNumberConflict,
)


def get_active_battlepass_data(
    db: Session,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Return the active season with all milestones and their computed statuses.
    Returns None if no active season.

    Milestone status (computed dynamically):
    - 'claimed'  → entry in user_battlepass_claims for this user
    - 'unlocked' → cab_earned_season >= cab_required AND not claimed
    - 'locked'   → cab_earned_season < cab_required
    """
    season_row = db.execute(
        text("SELECT id, name, ends_at FROM battlepass_seasons WHERE is_active = TRUE LIMIT 1")
    ).first()
    if not season_row:
        return None

    progress_row = db.execute(
        text("SELECT cab_earned_season FROM user_battlepass_progress WHERE user_id = :uid AND season_id = :sid"),
        {"uid": user_id, "sid": season_row.id},
    ).first()
    cab_earned_season = progress_row.cab_earned_season if progress_row else 0

    milestones = db.execute(
        text(
            "SELECT id, milestone_number, cab_required, reward_type, reward_value, subscriber_only "
            "FROM battlepass_milestones "
            "WHERE season_id = :sid "
            "ORDER BY cab_required ASC"
        ),
        {"sid": season_row.id},
    ).fetchall()

    claimed_ids = {
        row.milestone_id
        for row in db.execute(
            text("SELECT milestone_id FROM user_battlepass_claims WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchall()
    }

    milestone_list = []
    for m in milestones:
        if m.id in claimed_ids:
            status = "claimed"
        elif cab_earned_season >= m.cab_required:
            status = "unlocked"
        else:
            status = "locked"
        milestone_list.append(
            {
                "id": m.id,
                "milestone_number": m.milestone_number,
                "cab_required": m.cab_required,
                "reward_type": m.reward_type,
                "reward_value": m.reward_value,
                "subscriber_only": m.subscriber_only,
                "status": status,
            }
        )

    return {
        "season": {
            "id": season_row.id,
            "name": season_row.name,
            "ends_at": season_row.ends_at,
        },
        "cab_earned_season": cab_earned_season,
        "milestones": milestone_list,
    }


def get_milestone_for_claim(
    db: Session,
    user_id: uuid.UUID,
    milestone_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Return milestone data with computed status for claim validation.
    Returns None if no active season or milestone not found in active season.
    """
    row = db.execute(
        text(
            "SELECT m.id, m.cab_required, m.reward_type, m.reward_value, "
            "       m.subscriber_only, m.season_id "
            "FROM battlepass_milestones m "
            "JOIN battlepass_seasons s ON s.id = m.season_id "
            "WHERE m.id = :mid AND s.is_active = TRUE"
        ),
        {"mid": milestone_id},
    ).first()
    if not row:
        return None

    progress_row = db.execute(
        text("SELECT cab_earned_season FROM user_battlepass_progress WHERE user_id = :uid AND season_id = :sid"),
        {"uid": user_id, "sid": row.season_id},
    ).first()
    cab_earned_season = progress_row.cab_earned_season if progress_row else 0

    already_claimed = (
        db.execute(
            text("SELECT 1 FROM user_battlepass_claims WHERE user_id = :uid AND milestone_id = :mid"),
            {"uid": user_id, "mid": milestone_id},
        ).first()
        is not None
    )

    if already_claimed:
        status = "claimed"
    elif cab_earned_season >= row.cab_required:
        status = "unlocked"
    else:
        status = "locked"

    return {
        "id": row.id,
        "reward_type": row.reward_type,
        "reward_value": row.reward_value,
        "subscriber_only": row.subscriber_only,
        "status": status,
    }


def is_subscriber(db: Session, user_id: uuid.UUID) -> bool:
    """Return True if the user has an active non-expired subscription."""
    row = db.execute(
        text("SELECT 1 FROM subscriptions WHERE user_id = :uid AND status = 'active' AND expires_at > now() LIMIT 1"),
        {"uid": user_id},
    ).first()
    return row is not None


def get_newly_unlocked_bp_milestones(
    db: Session,
    user_id: uuid.UUID,
    season_id: uuid.UUID,
    cab_before: int,
) -> list[dict[str, Any]]:
    """
    Return battlepass milestones that just became claimable after a CAB award.

    A milestone is "just unlocked" when:
    - cab_required > cab_before  (wasn't unlocked before this award)
    - cab_required <= current cab_earned_season  (unlocked now)
    - not already claimed by this user

    Returns an empty list when no milestones cross the threshold.
    Callers use this list to fire notifications outside the DB transaction.
    """
    rows = db.execute(
        text(
            "SELECT m.id, m.milestone_number, m.reward_type "
            "FROM battlepass_milestones m "
            "JOIN user_battlepass_progress p "
            "  ON p.user_id = :uid AND p.season_id = :sid "
            "LEFT JOIN user_battlepass_claims c "
            "  ON c.milestone_id = m.id AND c.user_id = :uid "
            "WHERE m.season_id = :sid "
            "  AND m.cab_required > :before "
            "  AND m.cab_required <= p.cab_earned_season "
            "  AND c.id IS NULL"
        ),
        {"uid": user_id, "sid": season_id, "before": cab_before},
    ).fetchall()
    return [{"milestone_number": r.milestone_number, "reward_type": r.reward_type} for r in rows]


def insert_milestone_claim(
    db: Session,
    user_id: uuid.UUID,
    milestone_id: uuid.UUID,
) -> None:
    """Insert a user_battlepass_claims row — within the caller's transaction."""
    db.execute(
        text("INSERT INTO user_battlepass_claims (id, user_id, milestone_id) VALUES (:id, :uid, :mid)"),
        {"id": uuid.uuid4(), "uid": user_id, "mid": milestone_id},
    )


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------
def admin_list_seasons(db: Session) -> list[dict[str, Any]]:
    """Return all battlepass seasons (active + inactive), most recent first."""
    rows = db.execute(
        text(
            "SELECT id, season_number, name, started_at, ends_at, is_active "
            "FROM battlepass_seasons "
            "ORDER BY season_number DESC"
        )
    ).fetchall()
    return [
        {
            "id": str(r.id),
            "season_number": r.season_number,
            "name": r.name,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ends_at": r.ends_at.isoformat() if r.ends_at else None,
            "is_active": r.is_active,
        }
        for r in rows
    ]


def admin_create_season(
    db: Session,
    *,
    name: str,
    season_number: int,
    started_at: datetime,
    ends_at: datetime,
) -> uuid.UUID:
    """Insert a new battlepass season (is_active=False). Caller commits.

    Raises :
        SeasonNumberConflict — if season_number is already taken.
    """
    existing = db.execute(
        text("SELECT 1 FROM battlepass_seasons WHERE season_number = :num LIMIT 1"),
        {"num": season_number},
    ).first()
    if existing is not None:
        raise SeasonNumberConflict()

    season_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO battlepass_seasons "
            "    (id, season_number, name, started_at, ends_at, is_active) "
            "VALUES (:id, :num, :name, :start, :end, FALSE)"
        ),
        {
            "id": season_id,
            "num": season_number,
            "name": name,
            "start": started_at,
            "end": ends_at,
        },
    )
    return season_id


def admin_get_season(db: Session, season_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT id, season_number, name, started_at, ends_at, is_active FROM battlepass_seasons WHERE id = :sid"),
        {"sid": season_id},
    ).first()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "season_number": row.season_number,
        "name": row.name,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "is_active": row.is_active,
    }


def admin_activate_season(db: Session, season_id: uuid.UUID) -> None:
    """Set is_active=TRUE on a season, with a single-active invariant.

    Raises :
        SeasonNotFound — season does not exist.
        ActiveSeasonConflict — another season is already active.
    """
    target = db.execute(
        text("SELECT id, is_active FROM battlepass_seasons WHERE id = :sid FOR UPDATE"),
        {"sid": season_id},
    ).first()
    if target is None:
        raise SeasonNotFound()
    if target.is_active:
        # Idempotent : already active is a no-op.
        return

    # Lock-aware check for any other active season.
    other = db.execute(
        text("SELECT id FROM battlepass_seasons WHERE is_active = TRUE AND id <> :sid LIMIT 1"),
        {"sid": season_id},
    ).first()
    if other is not None:
        raise ActiveSeasonConflict()

    db.execute(
        text("UPDATE battlepass_seasons SET is_active = TRUE WHERE id = :sid"),
        {"sid": season_id},
    )


def admin_create_milestone(
    db: Session,
    *,
    season_id: uuid.UUID,
    milestone_number: int,
    cab_required: int,
    reward_type: str,
    reward_value: int,
    subscriber_only: bool,
) -> uuid.UUID:
    """Insert a milestone in a season. Caller commits.

    Raises :
        SeasonNotFound — season does not exist.
        MilestoneNumberConflict — milestone_number already used in this season.
    """
    season = db.execute(
        text("SELECT 1 FROM battlepass_seasons WHERE id = :sid"),
        {"sid": season_id},
    ).first()
    if season is None:
        raise SeasonNotFound()

    dup = db.execute(
        text("SELECT 1 FROM battlepass_milestones WHERE season_id = :sid AND milestone_number = :num LIMIT 1"),
        {"sid": season_id, "num": milestone_number},
    ).first()
    if dup is not None:
        raise MilestoneNumberConflict()

    milestone_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO battlepass_milestones "
            "    (id, season_id, milestone_number, cab_required, reward_type, "
            "     reward_value, subscriber_only) "
            "VALUES (:id, :sid, :num, :cab, :rtype, :rval, :sub)"
        ),
        {
            "id": milestone_id,
            "sid": season_id,
            "num": milestone_number,
            "cab": cab_required,
            "rtype": reward_type,
            "rval": reward_value,
            "sub": subscriber_only,
        },
    )
    return milestone_id
