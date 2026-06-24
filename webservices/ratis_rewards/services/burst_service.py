"""Burst service — passive XP scaling after a mission target is exceeded.

Concept (refonte 2026-05-09, spec :
``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``) :

* When the user's ``current_count`` exceeds the (post-Buffer) target,
  Burst paliers unlock automatically.
* Palier N is reached when ``current_count >= target × 2^N``.
* Each palier awards XP equal to ``xp_reward * 2^(N-1)`` (= total XP
  for paliers 1..N is ``xp_reward × (2^N − 1)``, geometric series).
* 0 CAB additional — Burst is pure XP / leaderboard fun.
* First Burst claim flips ``burst_locked = true`` permanently → user
  cannot Buffer this mission anymore (mutually exclusive choice).
* No cap : the leaderboard rewards extreme runs.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from ratis_core.exceptions import NotFound, PaymentRequired
from repositories.missions_repository import (
    get_user_mission_for_burst,
    update_burst_state,
    upsert_mission_xp_record,
)
from repositories.xp_repository import award_xp
from sqlalchemy.orm import Session

#: Hard ceiling on the Burst palier index. Each palier doubles the XP
#: (``xp_reward × 2^(N-1)``), so an unbounded ``N`` lets a pathological
#: ``current_count`` mint an absurd amount of XP. Palier 30 already
#: requires ``current_count >= target × 2^30`` (~1e9 actions) — far
#: beyond any legitimate run — so this cap never bites real users.
BURST_PALIER_HARD_CAP: int = 30


def compute_burst_paliers(
    *,
    current_count: int,
    target_count: int,
    current_burst_count: int = 0,
) -> int:
    """Return the total Burst palier index reached (= max N such that
    ``current_count >= target_count * 2^N``).

    Returns 0 if the target is not yet doubled (= no palier unlocked).
    The result is hard-capped at ``BURST_PALIER_HARD_CAP`` to bound the
    exponential XP grant.

    Pure function — no DB access, easy to test in isolation.
    """
    if target_count <= 0 or current_count < target_count * 2:
        return min(current_burst_count, BURST_PALIER_HARD_CAP)
    ratio = current_count / target_count
    palier = int(math.log2(ratio))
    return min(max(palier, current_burst_count), BURST_PALIER_HARD_CAP)


def claim_burst(
    db: Session,
    user_id: uuid.UUID,
    user_mission_id: uuid.UUID,
) -> dict[str, Any]:
    """Claim newly-unlocked Burst paliers (XP only) on this mission.

    Side effects :
        * award_xp with reason='mission_burst'
        * UPDATE user_missions : burst_count = paliers, burst_locked=TRUE
        * UPSERT mission_xp_records : leaderboard row

    Errors :
        404 mission_not_found
        402 no_burst_palier_unlocked  — current_count < target × 2 OR
                                        no new palier since last claim.
    """
    row = get_user_mission_for_burst(db, user_mission_id, user_id)
    if row is None:
        raise NotFound("mission_not_found")

    paliers = compute_burst_paliers(
        current_count=row.current_count,
        target_count=row.target_count,
        current_burst_count=row.burst_count,
    )

    if paliers <= row.burst_count:
        raise PaymentRequired("no_burst_palier_unlocked")

    # New paliers = (burst_count + 1 .. paliers). XP for palier k =
    # xp_reward × 2^(k-1). Sum is xp_reward × (2^paliers − 2^burst_count).
    xp_reward = int(row.xp_reward) if row.xp_reward else 0
    if xp_reward <= 0:
        # Pathological catalogue : no XP to award. Silent palier
        # progression — flip the lock but skip XP credit + leaderboard.
        update_burst_state(db, user_mission_id, new_burst_count=paliers)
        db.flush()
        return {
            "xp_awarded": 0,
            "burst_count_total": paliers,
            "burst_locked": True,
            "leaderboard_record_updated": False,
        }

    # Geometric sum : sum_{k=burst_count+1..paliers} xp_reward * 2^(k-1)
    #              = xp_reward * (2^paliers - 2^burst_count)
    xp_to_award = xp_reward * ((1 << paliers) - (1 << row.burst_count))

    award_xp(
        db,
        user_id,
        xp_to_award,
        "mission_burst",
        reference_id=user_mission_id,
        reference_type="user_mission",
        # Burst XP is "pure score" — do not stack the streak / community
        # multipliers (those already apply to mission_completed XP at
        # claim time). Otherwise we'd double-multiply.
        apply_streak_multiplier=False,
    )

    update_burst_state(db, user_mission_id, new_burst_count=paliers)

    upsert_mission_xp_record(
        db,
        user_id=user_id,
        mission_id=row.mission_id,
        user_mission_id=user_mission_id,
        xp_earned=xp_to_award,
        burst_count=paliers,
        buffer_count=row.buffer_count,
    )
    db.flush()

    return {
        "xp_awarded": xp_to_award,
        "burst_count_total": paliers,
        "burst_locked": True,
        "leaderboard_record_updated": True,
    }
