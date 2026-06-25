"""
Missions service — GET /rewards/missions (lazy gen), claim (multi-claim
cumulative + double gating), apply_buffer (= ex-Stonks), and freeze.

Buffer + Burst refonte (2026-05-09) :

- ``apply_buffer`` (renommé depuis ``apply_boost``) : multiplie le target
  par 2, augmente le CAB reward linéairement (R × (n+1)), étend la fenêtre
  de 1 jour, garde XP inchangé. Cap soft à n=3 daily, weekly refusé.
- ``claim_mission`` : multi-claim cumulatif via double gating
  (= min(paliers de progrès, jours écoulés) − portions déjà claim).
- ``claim_burst`` (nouveau, dans burst_service.py) : déblocage des
  paliers Burst après dépassement target. Lock anti-Buffer au 1er claim.

Spec : ``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ratis_core.exceptions import (
    BadRequest,
    Conflict,
    Gone,
    NotFound,
    PaymentRequired,
)
from ratis_core.settings import load_settings
from repositories.cab_repository import award_cab, debit_cab, get_balance
from repositories.missions_repository import (
    apply_buffer as _repo_apply_buffer,
)
from repositories.missions_repository import (
    apply_freeze,
    get_active_missions,
    get_period_start,
    get_user_mission_for_buffer,
    get_user_mission_for_claim,
    get_user_mission_for_freeze,
    get_user_missions,
    has_missions_for_period,
    insert_user_missions,
)
from repositories.xp_repository import award_xp
from sqlalchemy import Row, text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


def _buffer_n_max_daily() -> int:
    """Return the daily Buffer cap (n_max) from settings.

    Reads ``gamification.buffer.n_max_daily`` from ``ratis_settings.json``
    (or the ``app_settings`` table override) at request time — R19 : every
    variable parameter lives in settings, never a hardcoded module constant.
    Fail-fast if the key is missing so a misconfigured deploy surfaces at
    the first Buffer request.
    """
    return int(load_settings()["gamification"]["buffer"]["n_max_daily"])


def _generate_missions_for_user(
    db: Session,
    user_id: uuid.UUID,
    active_missions: Sequence[Row[Any]],
    period_start: date,
) -> None:
    """Select 1 mission per difficulty (no repeated ``(action_type,
    qualifier)`` couple) and insert.

    Phase B (PR #325) extended the uniqueness key from ``action_type``
    alone to the ``(action_type, qualifier)`` couple so the catalogue
    can offer e.g. ``product_identification`` (no filter) AND
    ``product_identification + attribute:organic`` simultaneously
    inside the same period.
    """
    selected_ids: list[uuid.UUID] = []
    used_couples: set[tuple[str, str | None]] = set()

    for difficulty in ("easy", "medium", "hard"):
        candidates = [
            m
            for m in active_missions
            if m.difficulty == difficulty and (m.action_type, getattr(m, "qualifier", None)) not in used_couples
        ]
        if candidates:
            mission = random.choice(candidates)
            selected_ids.append(mission.id)
            used_couples.add((mission.action_type, getattr(mission, "qualifier", None)))

    if selected_ids:
        insert_user_missions(db, user_id, selected_ids, period_start)


def get_missions(db: Session, user_id: uuid.UUID, today: date) -> dict[str, Any]:
    """
    Return daily and weekly missions for the user.

    Lazily generates user_missions from the catalogue if none exist for the period.
    The caller (route handler) is responsible for committing the transaction.
    """
    result: dict[str, Any] = {}

    for frequency, period_key in (("daily", "date"), ("weekly", "week_start")):
        period_start = get_period_start(frequency, today)

        if not has_missions_for_period(db, user_id, period_start, frequency):
            active = get_active_missions(db, frequency)
            if active:
                _generate_missions_for_user(db, user_id, active, period_start)

        missions = get_user_missions(db, user_id, period_start, frequency)
        result[frequency] = {
            period_key: period_start.isoformat(),
            "missions": [
                {
                    "id": str(m["id"]),
                    "action_type": m["action_type"],
                    "difficulty": m["difficulty"],
                    "frequency": m["frequency"],
                    "is_boostable": m["is_boostable"],
                    "target_count": m["target_count"],
                    "current_count": m["current_count"],
                    "cab_reward": m["cab_reward"],
                    "xp_reward": int(m["xp_reward"]),
                    "status": m["status"],
                    "buffer_count": m["buffer_count"],
                    "burst_count": m["burst_count"],
                    "burst_locked": m["burst_locked"],
                    "period_extended_until": (
                        m["period_extended_until"].isoformat().replace("+00:00", "Z")
                        if m["period_extended_until"] is not None
                        else None
                    ),
                    "portions_claimed": m["portions_claimed"],
                }
                for m in missions
            ],
        }

    return result


# ---------------------------------------------------------------------------
# Buffer + claim — refonte 2026-05-09
# ---------------------------------------------------------------------------


def apply_buffer(
    db: Session,
    user_id: uuid.UUID,
    user_mission_id: uuid.UUID,
) -> dict[str, Any]:
    """Apply one Buffer to a user mission.

    Effects (atomic) :
        buffer_count          += 1
        target_count          *= 2
        cab_reward             = R_original × (buffer_count + 1)
        period_extended_until  = period_start + (buffer_count + 1) days
        xp_reward              unchanged

    Conditions :
        * mission frequency == 'daily' (weekly refused)
        * status == 'pending'
        * buffer_count < gamification.buffer.n_max_daily (cap soft = 3)
        * burst_locked == false (no Burst claim yet on this mission)

    Buffer is free (no CAB cost). The cost-side of the original Stonks
    flow is gone in the V1 design.
    """
    row = get_user_mission_for_buffer(db, user_mission_id, user_id)
    if row is None:
        raise NotFound("mission_not_found")

    if row.frequency == "weekly":
        raise BadRequest("weekly_not_bufferable")
    if row.buffer_count >= _buffer_n_max_daily():
        raise Conflict("buffer_cap_reached")
    if row.burst_locked:
        raise Conflict("burst_locked")
    if row.status != "pending":
        raise Conflict("mission_not_pending")

    # R_original = current cab_reward / (buffer_count + 1) — recovered
    # from the per-user value so the formula stays self-contained.
    n_before = row.buffer_count
    r_original = row.cab_reward // (n_before + 1)
    new_buffer_count = n_before + 1
    new_target = row.target_count * 2
    new_cab_reward = r_original * (new_buffer_count + 1)
    period_start_dt = datetime.combine(row.period_start, datetime.min.time(), tzinfo=UTC)
    new_extended = period_start_dt + timedelta(days=new_buffer_count + 1)

    _repo_apply_buffer(
        db,
        user_mission_id,
        new_buffer_count=new_buffer_count,
        new_target_count=new_target,
        new_cab_reward=new_cab_reward,
        new_period_extended_until=new_extended,
    )
    db.flush()

    return {
        "buffer_count": new_buffer_count,
        "target_count": new_target,
        "cab_reward": new_cab_reward,
        "period_extended_until": new_extended.isoformat().replace("+00:00", "Z"),
    }


def claim_mission(
    db: Session,
    user_id: uuid.UUID,
    user_mission_id: uuid.UUID,
) -> dict[str, Any]:
    """Claim portions disponibles via multi-claim cumulatif + double gating.

    Logic :
        n            = buffer_count
        R_original   = cab_reward / (n + 1)
        palier_size  = target_count / (n + 1)

        paliers      = min(current_count // palier_size, n + 1)
        days_elapsed = min((now.date() - period_start).days + 1, n + 1)
        portions_av  = min(paliers, days_elapsed)        ← double gating
        to_claim     = portions_av - portions_claimed

    Cas dégénéré (mission classique sans buffer, n=0) :
        palier_size  = target_count
        portions_max = 1
    → comportement équivalent à l'ancien claim « tout-ou-rien ».

    Errors :
        404 mission_not_found
        409 already_claimed         — toutes portions récoltées
        410 mission_expired         — now > period_extended_until (or
                                      period_start + 1 day si pas
                                      bufferisée)
        402 no_portion_available_now— portions disponibles == 0 ou
                                      <= portions déjà récoltées

    XP = mission xp_reward, flat (le buffer ne la multiplie PAS — conforme
    au spec buffer-burst, décision n°2), créditée 1× au PREMIER claim de
    la mission (= quand ``portions_claimed == 0`` avant l'UPDATE de cette
    itération). Gater l'XP sur le claim final perdait définitivement la
    récompense d'une mission bufferisée partiellement claim puis expirée
    (audit RW-06). Reason = 'mission_completed'.
    """
    row = get_user_mission_for_claim(db, user_mission_id, user_id)
    if row is None:
        raise NotFound("mission_not_found")

    n = row.buffer_count
    if row.status == "claimed" or row.portions_claimed >= n + 1:
        raise Conflict("already_claimed")

    now = datetime.now(UTC)
    period_start_dt = datetime.combine(row.period_start, datetime.min.time(), tzinfo=UTC)
    deadline = (
        row.period_extended_until if row.period_extended_until is not None else period_start_dt + timedelta(days=1)
    )
    if now > deadline:
        raise Gone("mission_expired")

    # Compute portions available (double gating).
    # palier_size : exact division — target_count is always n+1 multiple
    # because each Buffer doubles the target from a base divisible-by-1.
    # Use integer floor on the ratio to be defensive against rounding.
    palier_size = row.target_count // (n + 1)
    if palier_size <= 0:
        # Pathological case — guard against div-by-zero if a mission
        # ships with target_count < (n+1). Treat as 0 paliers.
        paliers_atteints = 0
    else:
        paliers_atteints = min(row.current_count // palier_size, n + 1)

    days_elapsed = min((now.date() - row.period_start).days + 1, n + 1)
    portions_disponibles = min(paliers_atteints, days_elapsed)
    portions_a_claim = portions_disponibles - row.portions_claimed

    if portions_a_claim <= 0:
        raise PaymentRequired("no_portion_available_now")

    r_original = row.cab_reward // (n + 1)
    cab_to_award = portions_a_claim * r_original
    award_cab(
        db,
        user_id,
        cab_to_award,
        "mission_reward",
        reference_id=user_mission_id,
        reference_type="user_mission",
    )

    # Award XP at the FIRST claim only — once per user_mission, no
    # double-credit on partial claims. The first claim is detected on the
    # pre-UPDATE state (``row.portions_claimed == 0``). XP stays flat (the
    # buffer never multiplies it) and is captured before the UPDATE below
    # so a buffered mission claimed partially still receives its XP even
    # if the period expires before the final portion (audit RW-06).
    # Reason stays 'mission_completed' — Burst paliers use a separate
    # reason 'mission_burst' (cf burst_service.py).
    is_first_claim = row.portions_claimed == 0

    # Update portions_claimed + status.
    new_portions_claimed = portions_disponibles
    new_status = "claimed" if new_portions_claimed == n + 1 else row.status
    db.execute(
        text("UPDATE user_missions SET     portions_claimed = :pc,     status = :status WHERE id = :id"),
        {
            "pc": new_portions_claimed,
            "status": new_status,
            "id": user_mission_id,
        },
    )

    if is_first_claim:
        xp_amount = int(row.xp_reward) if row.xp_reward else 0
        if xp_amount > 0:
            award_xp(
                db,
                user_id,
                xp_amount,
                "mission_completed",
                reference_id=user_mission_id,
                reference_type="user_mission",
            )

    db.flush()
    new_balance = get_balance(db, user_id)
    return {
        "cab_awarded": cab_to_award,
        "portions_claimed_total": new_portions_claimed,
        "portions_remaining": (n + 1) - new_portions_claimed,
        "mission_status": new_status,
        "new_cab_balance": new_balance,
    }


# ---------------------------------------------------------------------------
# Mission freeze — unchanged by Buffer/Burst refonte
# ---------------------------------------------------------------------------


def freeze_mission(
    db: Session,
    user_id: uuid.UUID,
    user_mission_id: uuid.UUID,
    freeze_cost: int,
) -> dict[str, Any]:
    """
    Freeze a mission — debit CABs, set frozen_until to first day of next month.

    freeze_cost comes from ratis_settings["gamification"]["freeze_cost_cab"].
    """
    row = get_user_mission_for_freeze(db, user_mission_id, user_id)
    if row is None:
        raise NotFound("mission_not_found")
    if row.frozen_until is not None:
        raise Conflict("mission_already_frozen")
    if row.freeze_count >= 1:
        raise Conflict("freeze_limit_reached")

    debit_cab(
        db,
        user_id,
        freeze_cost,
        "mission_freeze",
        reference_id=user_mission_id,
        reference_type="user_mission",
    )
    apply_freeze(db, user_mission_id)
    db.flush()

    # Compute frozen_until (first of next month) for the response
    now = datetime.now(UTC)
    if now.month == 12:
        frozen_until = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        frozen_until = datetime(now.year, now.month + 1, 1, tzinfo=UTC)

    return {
        "frozen_until": frozen_until.isoformat().replace("+00:00", "Z"),
        "cost_paid_cab": freeze_cost,
    }
