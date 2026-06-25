"""
Missions repository — raw SQL for missions and user_missions tables.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repositories.exceptions import (
    MissionNotFound,
    MissionUniquenessConflict,
)


def get_active_missions(db: Session, frequency: str) -> Sequence[Row[Any]]:
    """Return active catalogue missions for a given frequency."""
    return db.execute(
        text(
            "SELECT id, action_type, qualifier, difficulty, "
            "       target_count, cab_reward "
            "FROM missions "
            "WHERE is_active = TRUE AND frequency = :freq"
        ),
        {"freq": frequency},
    ).fetchall()


def has_missions_for_period(
    db: Session,
    user_id: uuid.UUID,
    period_start: date,
    frequency: str,
) -> bool:
    """Return True if the user already has missions for this period."""
    row = db.execute(
        text(
            "SELECT 1 FROM user_missions um "
            "JOIN missions m ON m.id = um.mission_id "
            "WHERE um.user_id = :uid "
            "  AND um.period_start = :period_start "
            "  AND m.frequency = :frequency "
            "LIMIT 1"
        ),
        {"uid": user_id, "period_start": period_start, "frequency": frequency},
    ).first()
    return row is not None


def get_user_missions(
    db: Session,
    user_id: uuid.UUID,
    period_start: date,
    frequency: str,
) -> list[dict[str, Any]]:
    """Return user missions for a period joined with catalogue fields.

    Includes Buffer + Burst columns (PR #339 schema) so the FE (PR #343)
    can render the multi-claim / Burst overlay UI : ``frequency`` and
    ``is_boostable`` from the catalogue, plus per-user state
    (``buffer_count``, ``burst_count``, ``burst_locked``,
    ``period_extended_until``, ``portions_claimed``).
    """
    rows = db.execute(
        text(
            "SELECT um.id, m.action_type, m.difficulty, m.frequency, "
            "       m.is_boostable, um.target_count, um.current_count, "
            "       um.cab_reward, um.xp_reward, um.status, "
            "       um.buffer_count, um.burst_count, um.burst_locked, "
            "       um.period_extended_until, um.portions_claimed "
            "FROM user_missions um "
            "JOIN missions m ON m.id = um.mission_id "
            "WHERE um.user_id = :uid "
            "  AND um.period_start = :period_start "
            "  AND m.frequency = :frequency "
            "ORDER BY m.difficulty"
        ),
        {"uid": user_id, "period_start": period_start, "frequency": frequency},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def insert_user_missions(
    db: Session,
    user_id: uuid.UUID,
    mission_ids: list[uuid.UUID],
    period_start: date,
    xp_amounts: dict[str, int] | None = None,
) -> None:
    """Insert user_mission rows for selected catalogue missions.

    Copies target_count and cab_reward from the catalogue. xp_amounts maps
    action_type → xp base amount (from ratis_settings["xp"]); pass None to leave
    xp_reward at 0 (backward compatible).
    """
    for mid in mission_ids:
        mission_row = db.execute(
            text("SELECT action_type, target_count, cab_reward FROM missions WHERE id = :id"),
            {"id": mid},
        ).first()
        if not mission_row:
            continue
        xp_reward = 0
        if xp_amounts:
            key = f"xp_per_{mission_row.action_type}"
            xp_reward = xp_amounts.get(key, 0)
        db.execute(
            text(
                "INSERT INTO user_missions "
                "    (id, user_id, mission_id, period_start, current_count, status, "
                "     target_count, cab_reward, xp_reward) "
                "VALUES (:id, :uid, :mid, :period, 0, 'pending', "
                "        :target, :cab_reward, :xp_reward) "
                "ON CONFLICT (user_id, mission_id, period_start) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "mid": mid,
                "period": period_start,
                "target": mission_row.target_count,
                "cab_reward": mission_row.cab_reward,
                "xp_reward": xp_reward,
            },
        )


def get_user_mission_for_claim(
    db: Session,
    user_mission_id: uuid.UUID,
    user_id: uuid.UUID,
):
    """Fetch a user_mission with all reward + Buffer state, scoped to owner.

    Returns the per-user (post-buffer) ``cab_reward`` / ``xp_reward`` so
    multi-claim portions land on the correct denominator. Buffer columns
    are required by the double-gating logic in the service layer.

    Row-locked with ``FOR UPDATE`` (F-RW-4) : ``claim_mission`` reads
    ``portions_claimed`` then UPDATEs it ; two concurrent claims would
    otherwise both observe the same ``portions_claimed`` and double-credit
    CAB / XP. The lock serializes claims on the same user_mission row.
    """
    return db.execute(
        text(
            "SELECT um.id, um.status, um.cab_reward, um.xp_reward, "
            "       um.buffer_count, um.burst_count, um.burst_locked, "
            "       um.portions_claimed, um.period_start, "
            "       um.period_extended_until, um.target_count, "
            "       um.current_count "
            "FROM user_missions um "
            "WHERE um.id = :um_id AND um.user_id = :uid "
            "FOR UPDATE"
        ),
        {"um_id": user_mission_id, "uid": user_id},
    ).first()


def mark_mission_claimed(db: Session, user_mission_id: uuid.UUID) -> None:
    """Set status = 'claimed' on a user_mission."""
    db.execute(
        text("UPDATE user_missions SET status = 'claimed' WHERE id = :id"),
        {"id": user_mission_id},
    )


# ---------------------------------------------------------------------------
# Missions progress
# ---------------------------------------------------------------------------


def get_period_start(frequency: str, today: date) -> date:
    """Compute period_start for a given frequency and today's date."""
    if frequency == "daily":
        return today
    # weekly → monday of the current ISO week
    return today - timedelta(days=today.weekday())


def check_missions_progress(
    db: Session,
    user_id: uuid.UUID,
    action_type: str,
    today: date,
    increment: int = 1,
    qualifier: str | None = None,
) -> None:
    """
    Increment progress on every active catalogue mission whose
    ``(action_type, qualifier)`` tuple matches the event.

    Matching rules (phase B) :

    1. The mission's ``action_type`` must equal the event's.
    2. The mission's ``qualifier`` must match the event's qualifier
       under the following equivalences :

       a. mission.qualifier IS NULL → matches every event of that
          action_type, whatever the event's qualifier (a "no filter"
          mission counts everything).
       b. mission.qualifier carries a colon (``attribute:organic`` …)
          → only events whose qualifier equals it match.
       c. mission.qualifier is a "type tag" (``category`` / ``store``)
          → matches events whose qualifier starts with ``<tag>:`` and
          counts *distinct* values via ``user_missions.tracked_values``.
          Used by the ``scan_distinct`` action_type (and only there).

    For non-distinct missions (a / b above) the row's ``current_count``
    is incremented by ``increment``. For distinct missions (c) the
    qualifier value is appended to ``tracked_values`` (deduped) and
    ``current_count`` is set to the array length — repeating the same
    value never advances the mission.

    Caller must own the transaction. Raises if ``increment <= 0``.
    """
    if increment <= 0:
        raise ValueError(f"increment must be positive, got {increment}")

    for frequency in ("daily", "weekly"):
        period_start = get_period_start(frequency, today)

        # ------------------------------------------------------------------
        # Branch A — non-distinct missions (qualifier IS NULL or exact match
        # like ``attribute:organic``). Single upsert covers all of them.
        # ------------------------------------------------------------------
        db.execute(
            text(
                "INSERT INTO user_missions "
                "    (id, user_id, mission_id, period_start, current_count, status, "
                "     target_count, cab_reward, xp_reward) "
                "SELECT gen_random_uuid(), :uid, m.id, :period_start, "
                "       :increment, "
                "       CASE WHEN :increment >= m.target_count THEN 'completed' ELSE 'pending' END, "
                "       m.target_count, m.cab_reward, 0 "
                "FROM missions m "
                "WHERE m.is_active = TRUE "
                "  AND m.action_type = :action_type "
                "  AND m.frequency = :frequency "
                "  AND ( "
                "    m.qualifier IS NULL "
                "    OR m.qualifier = CAST(:qualifier AS text) "
                "  ) "
                # type-tag missions are routed through branch B below ;
                # COALESCE keeps NULL-qualifier missions in branch A.
                "  AND COALESCE(m.qualifier, '') NOT IN ('category', 'store') "
                "ON CONFLICT (user_id, mission_id, period_start) DO UPDATE "
                "    SET current_count = CASE "
                "            WHEN user_missions.status = 'claimed' "
                "                THEN user_missions.current_count "
                "            ELSE user_missions.current_count + :increment "
                "        END, "
                "        status = CASE "
                "            WHEN user_missions.status = 'claimed' THEN 'claimed' "
                "            WHEN user_missions.current_count + :increment "
                "                 >= user_missions.target_count "
                "            THEN 'completed' "
                "            ELSE user_missions.status "
                "        END "
                "WHERE user_missions.status <> 'claimed'"
            ),
            {
                "uid": user_id,
                "period_start": period_start,
                "action_type": action_type,
                "frequency": frequency,
                "increment": increment,
                "qualifier": qualifier,
            },
        )

        # ------------------------------------------------------------------
        # Branch B — distinct missions (``category`` / ``store`` type tag).
        # Only fires when the event qualifier carries a value that begins
        # with the type tag (``category:dairy``, ``store:<uuid>``).
        # ------------------------------------------------------------------
        if not qualifier:
            continue
        # Extract the type tag from the event qualifier (everything before
        # the first colon). ``attribute:organic`` → "attribute", which is
        # NOT a distinct-mission tag, so the LIKE filter on
        # ``m.qualifier`` will simply not match anything.
        tag, _, _ = qualifier.partition(":")
        if not tag or tag not in {"category", "store"}:
            continue

        # Fetch the matching distinct missions and update tracked_values
        # in Python — JSONB array dedup is awkward in pure SQL upserts.
        # The catalogue only carries 6 distinct missions today (see
        # MISSION_TEMPLATES_V1) so this stays cheap.
        rows = db.execute(
            text(
                "SELECT m.id, m.target_count, m.cab_reward "
                "FROM missions m "
                "WHERE m.is_active = TRUE "
                "  AND m.action_type = :action_type "
                "  AND m.frequency = :frequency "
                "  AND m.qualifier = :tag"
            ),
            {
                "action_type": action_type,
                "frequency": frequency,
                "tag": tag,
            },
        ).fetchall()

        for r in rows:
            _progress_distinct_mission(
                db,
                user_id=user_id,
                mission_id=r.id,
                period_start=period_start,
                target_count=r.target_count,
                cab_reward=r.cab_reward,
                value=qualifier,
            )


def _progress_distinct_mission(
    db: Session,
    *,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    period_start: date,
    target_count: int,
    cab_reward: int,
    value: str,
) -> None:
    """Append ``value`` to user_missions.tracked_values for a distinct
    mission, dedup, and set current_count = array length."""
    # Upsert — create the row on first event with the value seeded.
    seed_array = json.dumps([value])
    res = db.execute(
        text(
            "INSERT INTO user_missions "
            "  (id, user_id, mission_id, period_start, current_count, "
            "   status, target_count, cab_reward, xp_reward, tracked_values) "
            "VALUES (gen_random_uuid(), :uid, :mid, :period_start, 1, "
            "        CASE WHEN 1 >= :target THEN 'completed' ELSE 'pending' END, "
            "        :target, :cab, 0, CAST(:seed AS jsonb)) "
            "ON CONFLICT (user_id, mission_id, period_start) DO NOTHING "
            "RETURNING id"
        ),
        {
            "uid": user_id,
            "mid": mission_id,
            "period_start": period_start,
            "target": target_count,
            "cab": cab_reward,
            "seed": seed_array,
        },
    )
    if res.first() is not None:
        return  # Fresh insert — done.

    # Existing row : append-if-absent and recompute count.
    existing = db.execute(
        text(
            "SELECT id, status, tracked_values FROM user_missions "
            "WHERE user_id = :uid AND mission_id = :mid "
            "  AND period_start = :period_start "
            "FOR UPDATE"
        ),
        {
            "uid": user_id,
            "mid": mission_id,
            "period_start": period_start,
        },
    ).first()
    if existing is None:
        return  # Race condition tolerance.
    if existing.status == "claimed":
        return
    bag: list[str] = list(existing.tracked_values or [])
    if value in bag:
        return  # Duplicate — no-op.
    bag.append(value)
    new_count = len(bag)
    new_status = "completed" if new_count >= target_count else existing.status
    db.execute(
        text(
            "UPDATE user_missions SET "
            "    tracked_values = CAST(:bag AS jsonb), "
            "    current_count = :n, "
            "    status = :status "
            "WHERE id = :id"
        ),
        {
            "bag": json.dumps(bag),
            "n": new_count,
            "status": new_status,
            "id": existing.id,
        },
    )


# ---------------------------------------------------------------------------
# Buffer + Burst — refonte 2026-05-09 (replaces Stonks)
# ---------------------------------------------------------------------------


def get_user_mission_for_buffer(
    db: Session,
    user_mission_id: uuid.UUID,
    user_id: uuid.UUID,
):
    """Fetch a user_mission row + parent mission frequency for Buffer checks.

    Returns columns required by ``apply_buffer`` validation : status,
    buffer_count, burst_locked, target_count, cab_reward, period_start,
    plus the catalogue ``frequency`` (weekly missions are not bufferable).

    Row-locked with ``FOR UPDATE`` on the user_missions row (F-RW-10) :
    two concurrent ``apply_buffer`` would otherwise both pass the
    ``buffer_count < BUFFER_N_MAX_DAILY`` cap check and stack two
    increments, bypassing the n_max=3 contract. The lock on ``um``
    serializes Buffer applications on the same row ; ``OF um`` restricts
    the lock to the user_missions row (the catalogue join doesn't need
    a row lock).
    """
    return db.execute(
        text(
            "SELECT um.id, um.status, um.buffer_count, um.burst_locked, "
            "       um.target_count, um.cab_reward, um.xp_reward, "
            "       um.period_start, um.period_extended_until, "
            "       m.frequency "
            "FROM user_missions um "
            "JOIN missions m ON m.id = um.mission_id "
            "WHERE um.id = :um_id AND um.user_id = :uid "
            "FOR UPDATE OF um"
        ),
        {"um_id": user_mission_id, "uid": user_id},
    ).first()


def apply_buffer(
    db: Session,
    user_mission_id: uuid.UUID,
    *,
    new_buffer_count: int,
    new_target_count: int,
    new_cab_reward: int,
    new_period_extended_until,
) -> None:
    """Apply one Buffer to a user_mission.

    The values are computed in the service layer from the spec formulas
    (``target × 2``, ``cab × (n+1)``, ``period_start + (n+1) days``)
    and applied atomically here. xp_reward stays unchanged — Burst is
    the XP-scaling mechanic.
    """
    db.execute(
        text(
            "UPDATE user_missions SET "
            "    buffer_count          = :buffer, "
            "    target_count          = :target, "
            "    cab_reward            = :cab, "
            "    period_extended_until = :ext "
            "WHERE id = :id"
        ),
        {
            "buffer": new_buffer_count,
            "target": new_target_count,
            "cab": new_cab_reward,
            "ext": new_period_extended_until,
            "id": user_mission_id,
        },
    )


def get_user_mission_for_burst(
    db: Session,
    user_mission_id: uuid.UUID,
    user_id: uuid.UUID,
):
    """Fetch user_mission + parent mission_id for Burst claim handling.

    Row-locked with ``FOR UPDATE`` (F-RW-2) : ``claim_burst`` reads
    ``burst_count`` then UPDATEs it ; two concurrent claims would
    otherwise both compute the same paliers delta and double-credit XP
    to the leaderboard. The lock serializes Burst claims on the same
    user_mission row.
    """
    return db.execute(
        text(
            "SELECT um.id, um.user_id, um.mission_id, um.status, "
            "       um.buffer_count, um.burst_count, um.burst_locked, "
            "       um.target_count, um.current_count, um.xp_reward "
            "FROM user_missions um "
            "WHERE um.id = :um_id AND um.user_id = :uid "
            "FOR UPDATE"
        ),
        {"um_id": user_mission_id, "uid": user_id},
    ).first()


def update_burst_state(
    db: Session,
    user_mission_id: uuid.UUID,
    *,
    new_burst_count: int,
) -> None:
    """Set burst_count to the resolved palier total + lock anti-Buffer.

    The lock is irreversible (= burst_locked never goes back to FALSE).
    """
    db.execute(
        text("UPDATE user_missions SET     burst_count  = :burst,     burst_locked = TRUE WHERE id = :id"),
        {"burst": new_burst_count, "id": user_mission_id},
    )


def upsert_mission_xp_record(
    db: Session,
    *,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    user_mission_id: uuid.UUID,
    xp_earned: Any,
    burst_count: int,
    buffer_count: int,
) -> None:
    """Upsert mission_xp_records — 1 row par user_mission, agrège l'XP totale.

    Behaviour :
        * 1er claim Burst sur cette user_mission → INSERT
        * claims suivants → UPDATE inplace (xp_earned += new_xp,
          burst_count = max). The UNIQUE (user_mission_id) constraint
          guarantees idempotence.

    Caller (= burst_service.claim_burst) sums the new palier XP — we
    just persist the cumulative value.
    """
    db.execute(
        text(
            "INSERT INTO mission_xp_records "
            "    (id, user_id, mission_id, user_mission_id, xp_earned, "
            "     burst_count, buffer_count) "
            "VALUES (:id, :uid, :mid, :umid, :xp, :burst, :buffer) "
            "ON CONFLICT (user_mission_id) DO UPDATE "
            "SET xp_earned   = mission_xp_records.xp_earned + EXCLUDED.xp_earned, "
            "    burst_count = GREATEST(EXCLUDED.burst_count, "
            "                           mission_xp_records.burst_count), "
            "    buffer_count= EXCLUDED.buffer_count"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "mid": mission_id,
            "umid": user_mission_id,
            "xp": xp_earned,
            "burst": burst_count,
            "buffer": buffer_count,
        },
    )


# ---------------------------------------------------------------------------
# Mission freeze
# ---------------------------------------------------------------------------


def get_user_mission_for_freeze(
    db: Session,
    user_mission_id: uuid.UUID,
    user_id: uuid.UUID,
):
    """Fetch a user_mission's freeze state, filtered by ownership.

    Uses ``SELECT ... FOR UPDATE`` to serialise concurrent freeze attempts on
    the same user_mission row. Without the lock, two parallel requests can
    both read ``frozen_until = NULL`` and ``freeze_count = 0``, both pass the
    validation checks, both debit ``freeze_cost`` CAB, and both increment
    ``freeze_count`` — leaving the user double-charged for a single freeze.
    Same race shape as F-RW-2 / F-RW-4 / F-RW-10 (cf PR #390).
    """
    return db.execute(
        text(
            "SELECT id, status, frozen_until, freeze_count "
            "FROM user_missions "
            "WHERE id = :um_id AND user_id = :uid "
            "FOR UPDATE"
        ),
        {"um_id": user_mission_id, "uid": user_id},
    ).first()


def apply_freeze(db: Session, user_mission_id: uuid.UUID) -> None:
    """Set frozen_until to first day of next month and increment freeze_count."""
    db.execute(
        text(
            "UPDATE user_missions SET "
            "    frozen_until = DATE_TRUNC('month', now()) + INTERVAL '1 month', "
            "    freeze_count = freeze_count + 1 "
            "WHERE id = :id"
        ),
        {"id": user_mission_id},
    )


def get_mission_id_for_user_mission(db: Session, user_mission_id: uuid.UUID) -> uuid.UUID | None:
    """Return the mission_id for a user_mission row."""
    row = db.execute(
        text("SELECT mission_id FROM user_missions WHERE id = :id"),
        {"id": user_mission_id},
    ).first()
    return row.mission_id if row else None


# ---------------------------------------------------------------------------
# Admin helpers — mission catalogue (templates) CRUD
# ---------------------------------------------------------------------------
def admin_list_missions(
    db: Session,
    *,
    frequency: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return paginated mission catalogue rows + total count after filters."""
    # Fully-literal SQL, no string concatenation — the optional filters use
    # the ``(:param IS NULL OR col = :param)`` idiom so the same statement
    # serves all 4 filter combinations (S608-clean).
    params: dict[str, Any] = {
        "freq": frequency,
        "active": is_active,
        "lim": limit,
        "off": offset,
    }
    total_row = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM missions "
            "WHERE (CAST(:freq AS text) IS NULL OR frequency = :freq) "
            "  AND (CAST(:active AS boolean) IS NULL OR is_active = :active)"
        ),
        params,
    ).first()
    total = int(total_row.n) if total_row else 0

    rows = db.execute(
        text(
            "SELECT id, action_type, frequency, difficulty, target_count, "
            "       cab_reward, is_active, is_boostable "
            "FROM missions "
            "WHERE (CAST(:freq AS text) IS NULL OR frequency = :freq) "
            "  AND (CAST(:active AS boolean) IS NULL OR is_active = :active) "
            "ORDER BY frequency, difficulty, action_type "
            "LIMIT :lim OFFSET :off"
        ),
        params,
    ).fetchall()
    return (
        [
            {
                "id": str(r.id),
                "action_type": r.action_type,
                "frequency": r.frequency,
                "difficulty": r.difficulty,
                "target_count": r.target_count,
                "cab_reward": r.cab_reward,
                "is_active": r.is_active,
                "is_boostable": r.is_boostable,
            }
            for r in rows
        ],
        total,
    )


def admin_create_mission(
    db: Session,
    *,
    action_type: str,
    frequency: str,
    difficulty: str,
    target_count: int,
    cab_reward: int,
    is_active: bool = True,
    is_boostable: bool = True,
) -> uuid.UUID:
    """Insert a mission template. Caller commits.

    Uniqueness is enforced by the DB ``uq_mission`` UNIQUE constraint
    (``action_type, qualifier, frequency, difficulty`` with
    ``NULLS NOT DISTINCT`` — KP-64). Relying on the constraint instead of a
    pre-SELECT avoids the TOCTOU race where two concurrent inserts both pass
    the check, and is qualifier-aware for free.

    Raises :
        MissionUniquenessConflict — the uq_mission tuple already exists.
    """
    mission_id = uuid.uuid4()
    try:
        db.execute(
            text(
                "INSERT INTO missions "
                "    (id, action_type, frequency, difficulty, target_count, "
                "     cab_reward, is_active, is_boostable) "
                "VALUES (:id, :a, :f, :d, :target, :reward, :active, :boost)"
            ),
            {
                "id": mission_id,
                "a": action_type,
                "f": frequency,
                "d": difficulty,
                "target": target_count,
                "reward": cab_reward,
                "active": is_active,
                "boost": is_boostable,
            },
        )
        db.flush()  # trigger uq_mission check (atomicity guaranteed by DB)
    except IntegrityError:
        db.rollback()
        raise MissionUniquenessConflict()
    return mission_id


def admin_get_mission(db: Session, mission_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            "SELECT id, action_type, frequency, difficulty, target_count, "
            "       cab_reward, is_active, is_boostable "
            "FROM missions WHERE id = :id"
        ),
        {"id": mission_id},
    ).first()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "action_type": row.action_type,
        "frequency": row.frequency,
        "difficulty": row.difficulty,
        "target_count": row.target_count,
        "cab_reward": row.cab_reward,
        "is_active": row.is_active,
        "is_boostable": row.is_boostable,
    }


def admin_update_mission(
    db: Session,
    mission_id: uuid.UUID,
    *,
    fields: dict[str, Any],
) -> None:
    """Partial UPDATE of a mission row. Caller commits.

    ``fields`` keys must already be validated against an allowlist by the
    caller (route layer) — this helper trusts the keys.

    Raises :
        MissionNotFound — mission_id does not exist.
        MissionUniquenessConflict — (action_type, frequency, difficulty)
            tuple after the update collides with another row.
    """
    if not fields:
        # No-op : ensure the row exists for proper 404.
        existing = db.execute(
            text("SELECT 1 FROM missions WHERE id = :id"),
            {"id": mission_id},
        ).first()
        if existing is None:
            raise MissionNotFound()
        return

    current = db.execute(
        text("SELECT action_type, frequency, difficulty FROM missions WHERE id = :id FOR UPDATE"),
        {"id": mission_id},
    ).first()
    if current is None:
        raise MissionNotFound()

    # Compute resulting unique tuple ; if it changed, ensure no collision.
    new_action = fields.get("action_type", current.action_type)
    new_freq = fields.get("frequency", current.frequency)
    new_diff = fields.get("difficulty", current.difficulty)
    if (new_action, new_freq, new_diff) != (
        current.action_type,
        current.frequency,
        current.difficulty,
    ):
        dup = db.execute(
            text(
                "SELECT 1 FROM missions "
                "WHERE action_type = :a AND frequency = :f AND difficulty = :d "
                "  AND id <> :id "
                "LIMIT 1"
            ),
            {"a": new_action, "f": new_freq, "d": new_diff, "id": mission_id},
        ).first()
        if dup is not None:
            raise MissionUniquenessConflict()

    # Fully-literal UPDATE — every column can be left unchanged by passing
    # NULL for its parameter (``COALESCE`` keeps the existing value). This
    # avoids any string concatenation of column names (S608-clean).
    db.execute(
        text(
            "UPDATE missions SET "
            "  action_type  = COALESCE(CAST(:action_type AS text), action_type), "
            "  frequency    = COALESCE(CAST(:frequency AS text), frequency), "
            "  difficulty   = COALESCE(CAST(:difficulty AS text), difficulty), "
            "  target_count = COALESCE(CAST(:target_count AS integer), target_count), "
            "  cab_reward   = COALESCE(CAST(:cab_reward AS integer), cab_reward), "
            "  is_active    = COALESCE(CAST(:is_active AS boolean), is_active), "
            "  is_boostable = COALESCE(CAST(:is_boostable AS boolean), is_boostable) "
            "WHERE id = :id"
        ),
        {
            "action_type": fields.get("action_type"),
            "frequency": fields.get("frequency"),
            "difficulty": fields.get("difficulty"),
            "target_count": fields.get("target_count"),
            "cab_reward": fields.get("cab_reward"),
            "is_active": fields.get("is_active"),
            "is_boostable": fields.get("is_boostable"),
            "id": mission_id,
        },
    )
