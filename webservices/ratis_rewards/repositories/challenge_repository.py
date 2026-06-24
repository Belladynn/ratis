"""
Community challenge repository — raw SQL for atomicity.

Public API (user-facing):
  get_active_challenge_with_state(db, user_id)                        → dict | None
  maybe_increment_challenge(db, user_id, action_type, context=None)   → None
  get_active_community_multiplier(db, user_id, applies_to)            → float
  get_active_community_multipliers(db, user_id)          → (cab_mult, xp_mult)
  claim_milestone(db, user_id, milestone_id)             → dict  (reward spec only — no side-effects)
  create_community_multiplier(db, challenge_id, user_id, multiplier, applies_to, duration_hours)

Public API (admin):
  create_challenge(db, ...)                              → uuid.UUID
  create_challenge_milestone(db, ...)                    → uuid.UUID
  activate_challenge(db, challenge_id)                   → None  (raises ActiveChallengeConflict, ChallengeNotFound)
  deactivate_challenge(db, challenge_id)                 → None  (raises ChallengeNotFound)
  list_challenges_with_state(db)                         → list[dict]
  get_challenge_by_id(db, challenge_id)                  → dict | None

Dependency direction: challenge_repository has NO dependency on cab_repository or xp_repository.
Callers (routes) are responsible for applying rewards after claim_milestone returns.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repositories.exceptions import (
    ActiveChallengeConflict,
    ChallengeExpired,
    ChallengeNotFound,
    MilestoneAlreadyClaimed,
    MilestoneLocked,
    MilestoneNotFound,
)
from repositories.notification_repository import enqueue_notification

# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_active_challenge_with_state(db: Session, user_id: uuid.UUID) -> dict | None:
    """
    Return the full state of the active (or frozen) challenge, or None.

    Expired challenges (past ends_at + grace_period_days) return None → 404.
    """
    row = db.execute(
        text(
            "SELECT c.id, c.title, c.description, c.action_type, c.objective, "
            "       c.starts_at, c.ends_at, c.grace_period_days, "
            "       COALESCE(cp.current_count, 0) AS current_count "
            "FROM community_challenges c "
            "LEFT JOIN community_challenge_progress cp ON cp.challenge_id = c.id "
            "WHERE c.is_active = TRUE "
            "LIMIT 1"
        )
    ).first()
    if not row:
        return None

    now = datetime.now(UTC)
    ends_at: datetime = row.ends_at
    # Ensure tz-aware (PostgreSQL TIMESTAMPTZ always returns tz-aware via psycopg3)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=UTC)
    grace_until = ends_at + timedelta(days=row.grace_period_days)

    if now < ends_at:
        status = "active"
    elif now < grace_until:
        status = "frozen"
    else:
        return None  # expired — caller returns 404

    # Milestones with unlocked/claimed per-user state
    milestones = db.execute(
        text(
            "SELECT m.id, m.threshold, m.reward_type, m.reward_value, "
            "       m.label, m.sort_order, "
            "       (m.threshold <= :current_count) AS unlocked, "
            "       (cl.id IS NOT NULL) AS claimed "
            "FROM community_challenge_milestones m "
            "LEFT JOIN community_challenge_claims cl "
            "  ON cl.milestone_id = m.id AND cl.user_id = :uid "
            "WHERE m.challenge_id = :cid "
            "ORDER BY m.sort_order"
        ),
        {
            "cid": row.id,
            "uid": user_id,
            "current_count": row.current_count,
        },
    ).fetchall()

    return {
        "id": str(row.id),
        "title": row.title,
        "description": row.description,
        "action_type": row.action_type,
        "objective": row.objective,
        "current_count": row.current_count,
        "status": status,
        "ends_at": ends_at.isoformat(),
        "claims_until": grace_until.isoformat(),
        "milestones": [
            {
                "id": str(m.id),
                "threshold": m.threshold,
                "reward_type": m.reward_type,
                "reward_value": m.reward_value,  # JSONB → dict via psycopg3
                "label": m.label,
                "sort_order": m.sort_order,
                "unlocked": bool(m.unlocked),
                "claimed": bool(m.claimed),
            }
            for m in milestones
        ],
    }


def get_active_community_multiplier(db: Session, user_id: uuid.UUID, applies_to: str) -> float:
    """
    Return the community multiplier active for this user and applies_to scope.

    Returns 0.0 if no multiplier is active.
    A row with applies_to='both' matches both 'cab' and 'xp' queries.

    Use get_active_community_multipliers() when both scopes are needed in the
    same transaction — it fetches both values in a single query.
    """
    row = db.execute(
        text(
            "SELECT multiplier "
            "FROM community_multipliers "
            "WHERE user_id = :uid "
            "  AND (applies_to = :applies OR applies_to = 'both') "
            "  AND active_from <= now() "
            "  AND active_until > now() "
            "LIMIT 1"
        ),
        {"uid": user_id, "applies": applies_to},
    ).first()
    return float(row.multiplier) if row else 0.0


def get_active_community_multipliers(db: Session, user_id: uuid.UUID) -> tuple[float, float]:
    """
    Return (cab_multiplier, xp_multiplier) in a single query.

    Use this when award_cab and award_xp are both called in the same transaction
    to avoid two separate DB round-trips.
    """
    row = db.execute(
        text(
            "SELECT multiplier, applies_to "
            "FROM community_multipliers "
            "WHERE user_id = :uid "
            "  AND active_from <= now() "
            "  AND active_until > now() "
            "LIMIT 1"
        ),
        {"uid": user_id},
    ).first()
    if not row:
        return 0.0, 0.0
    m = float(row.multiplier)
    match row.applies_to:
        case "cab":
            return m, 0.0
        case "xp":
            return 0.0, m
        case _:  # "both"
            return m, m


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def maybe_increment_challenge(
    db: Session,
    user_id: uuid.UUID,
    action_type: str,
    context: dict | None = None,
) -> None:
    """
    Atomically increment the active challenge's progress by 1 if it matches,
    and enqueue a `challenge_milestone_unlocked` notification for each newly-
    crossed threshold — in the same transaction as the increment.

    Matching rules:
    - action_type must match c.action_type
    - now() < c.ends_at  (ACTIVE phase only — not FROZEN)
    - If c.action_filter IS NULL  → always matches
    - If c.action_filter IS NOT NULL → context must contain all filter key-values
      (PostgreSQL JSONB containment: action_filter <@ context).
      If context=None and action_filter IS NOT NULL → no match (conservative).

    Notifications are enqueued via the outbox table (DA-15) so they survive
    crashes and don't block the DB commit on HTTP latency.
    """
    rows = db.execute(
        text(
            "WITH incremented AS ( "
            "    UPDATE community_challenge_progress cp "
            "    SET current_count = current_count + 1, last_updated_at = now() "
            "    FROM community_challenges c "
            "    WHERE cp.challenge_id = c.id "
            "      AND c.is_active = TRUE "
            "      AND c.action_type = :action_type "
            "      AND now() < c.ends_at "
            "      AND (c.action_filter IS NULL OR c.action_filter <@ CAST(:context AS jsonb)) "
            "    RETURNING cp.challenge_id, cp.current_count "
            ") "
            "SELECT m.id AS milestone_id, m.label "
            "FROM incremented i "
            "JOIN community_challenge_milestones m ON m.challenge_id = i.challenge_id "
            "WHERE m.threshold = i.current_count"
        ),
        {
            "action_type": action_type,
            "context": json.dumps(context) if context is not None else None,
        },
    ).fetchall()
    for r in rows:
        enqueue_notification(db, user_id, "challenge_milestone_unlocked", {"label": r.label})


def claim_milestone(db: Session, user_id: uuid.UUID, milestone_id: uuid.UUID) -> dict:
    """
    Claim a challenge milestone for a user.

    Validation order:
      1. Milestone + active challenge must exist → MilestoneNotFound (404)
      2. Challenge not expired (past grace period) → ChallengeExpired (409)
      3. Progress has reached threshold → MilestoneLocked (409)
      4. Not already claimed by this user → MilestoneAlreadyClaimed (409)
      5. Award reward
      6. Insert claim row

    Returns {"milestone_id", "reward_type", "reward_value"}.
    """
    # 1. Load milestone + its active challenge
    row = db.execute(
        text(
            "SELECT m.id, m.challenge_id, m.threshold, m.reward_type, m.reward_value, "
            "       c.ends_at, c.grace_period_days "
            "FROM community_challenge_milestones m "
            "JOIN community_challenges c "
            "  ON c.id = m.challenge_id AND c.is_active = TRUE "
            "WHERE m.id = :mid"
        ),
        {"mid": milestone_id},
    ).first()
    if not row:
        raise MilestoneNotFound()

    # 2. Check not expired (claims window still open)
    now = datetime.now(UTC)
    ends_at: datetime = row.ends_at
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=UTC)
    grace_until = ends_at + timedelta(days=row.grace_period_days)
    if now >= grace_until:
        raise ChallengeExpired()

    # 3. Check threshold reached
    progress = (
        db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": row.challenge_id},
        ).scalar()
        or 0
    )
    if progress < row.threshold:
        raise MilestoneLocked()

    # 4. Check not already claimed
    existing = db.execute(
        text("SELECT id FROM community_challenge_claims WHERE milestone_id = :mid AND user_id = :uid"),
        {"mid": milestone_id, "uid": user_id},
    ).first()
    if existing:
        raise MilestoneAlreadyClaimed()

    # 5. Insert claim. The step-4 SELECT does not close the concurrency
    #    window — two requests can both pass it (each in its own READ
    #    COMMITTED snapshot) and reach this INSERT. The UNIQUE constraint
    #    ``uq_challenge_claims_milestone_user`` blocks the double-claim,
    #    but raises a raw IntegrityError. Translate it into the domain
    #    exception so the route returns 409, not 500.
    try:
        db.execute(
            text(
                "INSERT INTO community_challenge_claims "
                "    (id, challenge_id, milestone_id, user_id) "
                "VALUES (:id, :cid, :mid, :uid)"
            ),
            {
                "id": uuid.uuid4(),
                "cid": row.challenge_id,
                "mid": milestone_id,
                "uid": user_id,
            },
        )
        db.flush()  # surface the UNIQUE violation here, inside the try
    except IntegrityError as exc:
        raise MilestoneAlreadyClaimed() from exc

    # Return reward spec — caller is responsible for applying the reward.
    # This keeps challenge_repository free of dependencies on cab/xp repositories.
    return {
        "milestone_id": str(milestone_id),
        "reward_type": row.reward_type,
        "reward_value": row.reward_value,
        "challenge_id": row.challenge_id,
    }


def create_community_multiplier(
    db: Session,
    challenge_id: uuid.UUID,
    user_id: uuid.UUID,
    multiplier: float,
    applies_to: str,
    duration_hours: int,
) -> None:
    """
    Insert a community_multipliers row for a claimed multiplier milestone.

    Called by the route after claim_milestone returns reward_type='multiplier'.
    """
    active_from = datetime.now(UTC)
    active_until = active_from + timedelta(hours=duration_hours)
    db.execute(
        text(
            "INSERT INTO community_multipliers "
            "    (id, challenge_id, user_id, multiplier, applies_to, "
            "     active_from, active_until) "
            "VALUES (:id, :cid, :uid, :mult, :applies, :from, :until)"
        ),
        {
            "id": uuid.uuid4(),
            "cid": challenge_id,
            "uid": user_id,
            "mult": multiplier,
            "applies": applies_to,
            "from": active_from,
            "until": active_until,
        },
    )


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------


def create_challenge(
    db: Session,
    *,
    title: str,
    description: str | None,
    action_type: str,
    action_filter: dict[str, Any] | None,
    objective: int,
    starts_at: datetime,
    ends_at: datetime,
    grace_period_days: int,
) -> uuid.UUID:
    """
    Create a new community challenge (inactive by default) and its progress row.

    Returns the new challenge UUID.
    """
    cid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO community_challenges "
            "    (id, title, description, action_type, action_filter, objective, "
            "     starts_at, ends_at, grace_period_days, is_active) "
            "VALUES (:id, :title, :desc, :action_type, CAST(:action_filter AS jsonb), "
            "        :objective, :starts_at, :ends_at, :grace, FALSE)"
        ),
        {
            "id": cid,
            "title": title,
            "desc": description,
            "action_type": action_type,
            "action_filter": json.dumps(action_filter) if action_filter is not None else None,
            "objective": objective,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "grace": grace_period_days,
        },
    )
    db.execute(
        text("INSERT INTO community_challenge_progress (challenge_id, current_count) VALUES (:cid, 0)"),
        {"cid": cid},
    )
    return cid


def create_challenge_milestone(
    db: Session,
    *,
    challenge_id: uuid.UUID,
    threshold: int,
    reward_type: str,
    reward_value: dict[str, Any],
    label: str | None,
    sort_order: int,
) -> uuid.UUID:
    """
    Add a milestone to a challenge.

    Raises ChallengeNotFound if challenge_id does not exist.
    """
    # Verify challenge exists
    exists = db.execute(
        text("SELECT 1 FROM community_challenges WHERE id = :cid"),
        {"cid": challenge_id},
    ).first()
    if not exists:
        raise ChallengeNotFound()

    mid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO community_challenge_milestones "
            "    (id, challenge_id, threshold, reward_type, reward_value, label, sort_order) "
            "VALUES (:id, :cid, :threshold, :rtype, CAST(:rvalue AS jsonb), :label, :sort)"
        ),
        {
            "id": mid,
            "cid": challenge_id,
            "threshold": threshold,
            "rtype": reward_type,
            "rvalue": json.dumps(reward_value),
            "label": label,
            "sort": sort_order,
        },
    )
    return mid


def activate_challenge(db: Session, challenge_id: uuid.UUID) -> None:
    """
    Set is_active=TRUE on a challenge.

    Raises:
      ChallengeNotFound       — challenge_id does not exist
      ActiveChallengeConflict — another challenge is already active
                                (enforced by unique partial index — declared in
                                both the Alembic migration AND CommunityChallenge
                                __table_args__ so create_all covers the test DB)
    """
    exists = db.execute(
        text("SELECT 1 FROM community_challenges WHERE id = :cid"),
        {"cid": challenge_id},
    ).first()
    if not exists:
        raise ChallengeNotFound()

    try:
        db.execute(
            text("UPDATE community_challenges SET is_active = TRUE WHERE id = :cid"),
            {"cid": challenge_id},
        )
        db.flush()  # trigger unique index check (atomicity guaranteed by DB)
    except IntegrityError as exc:
        # The repository does not own the transaction — the caller's
        # ``db_transaction`` performs the rollback. We only translate the
        # raw IntegrityError into a domain exception.
        raise ActiveChallengeConflict() from exc


def deactivate_challenge(db: Session, challenge_id: uuid.UUID) -> None:
    """
    Set is_active=FALSE on a challenge.

    Raises ChallengeNotFound if challenge_id does not exist.
    """
    result = db.execute(
        text("UPDATE community_challenges SET is_active = FALSE WHERE id = :cid"),
        {"cid": challenge_id},
    )
    if result.rowcount == 0:
        raise ChallengeNotFound()


def list_challenges_with_state(db: Session) -> list[dict]:
    """
    Return all challenges with computed status, current_count, and milestone_count.

    Status values: 'active', 'frozen', 'expired', 'inactive'.
    """
    rows = db.execute(
        text(
            "SELECT c.id, c.title, c.description, c.action_type, c.action_filter, "
            "       c.objective, c.starts_at, c.ends_at, c.grace_period_days, c.is_active, "
            "       COALESCE(cp.current_count, 0) AS current_count, "
            "       COUNT(m.id) AS milestone_count "
            "FROM community_challenges c "
            "LEFT JOIN community_challenge_progress cp ON cp.challenge_id = c.id "
            "LEFT JOIN community_challenge_milestones m ON m.challenge_id = c.id "
            "GROUP BY c.id, cp.current_count "
            "ORDER BY c.starts_at DESC"
        )
    ).fetchall()

    now = datetime.now(UTC)
    result = []
    for row in rows:
        if not row.is_active:
            status = "inactive"
        else:
            ends_at = row.ends_at
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=UTC)
            grace_until = ends_at + timedelta(days=row.grace_period_days)
            if now < ends_at:
                status = "active"
            elif now < grace_until:
                status = "frozen"
            else:
                status = "expired"

        result.append(
            {
                "id": str(row.id),
                "title": row.title,
                "description": row.description,
                "action_type": row.action_type,
                "action_filter": row.action_filter,
                "objective": row.objective,
                "starts_at": row.starts_at.isoformat(),
                "ends_at": row.ends_at.isoformat(),
                "grace_period_days": row.grace_period_days,
                "status": status,
                "current_count": row.current_count,
                "milestone_count": row.milestone_count,
            }
        )
    return result


def get_challenge_by_id(db: Session, challenge_id: uuid.UUID) -> dict | None:
    """Return a single challenge dict (no user-specific state), or None."""
    row = db.execute(
        text(
            "SELECT c.id, c.title, c.description, c.action_type, c.action_filter, "
            "       c.objective, c.starts_at, c.ends_at, c.grace_period_days, c.is_active, "
            "       COALESCE(cp.current_count, 0) AS current_count "
            "FROM community_challenges c "
            "LEFT JOIN community_challenge_progress cp ON cp.challenge_id = c.id "
            "WHERE c.id = :cid"
        ),
        {"cid": challenge_id},
    ).first()
    if not row:
        return None
    return {
        "id": str(row.id),
        "title": row.title,
        "description": row.description,
        "action_type": row.action_type,
        "action_filter": row.action_filter,
        "objective": row.objective,
        "starts_at": row.starts_at.isoformat(),
        "ends_at": row.ends_at.isoformat(),
        "grace_period_days": row.grace_period_days,
        "is_active": row.is_active,
        "current_count": row.current_count,
    }
