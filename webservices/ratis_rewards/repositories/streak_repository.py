"""
Streak repository — Feed Jack streak mechanics.

All write operations work within the caller's session transaction.
The caller is responsible for commit() after all operations succeed.

Streak rules (DA-09 / DA-10 / DA-11):
  gap_days = (today - last_fed_at).days - 1
  gap_days <= 0                              → normal feed, streak += 1
  0 < gap_days <= food_reserves              → auto-freeze, consume reserves, streak += 1
  gap_days == 1 AND food_reserves == 0       → needs_repair: true (raises StreakNeedsRepair)
  gap_days >= 2 without coverage             → streak resets to 1
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ratis_core.database import affected_rows
from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.exceptions import (
    InsufficientBalance,
    ReserveLimitExceeded,
    StreakNeedsRepair,
    StreakNotInRepairState,
)

logger = logging.getLogger(__name__)

#: Default IANA timezone used when none is stored or the stored value is
#: invalid. Streak "day" boundaries are computed in this zone.
_DEFAULT_TZ = "Europe/Paris"


def _today_in_tz(tz: str | None) -> date:
    """Return the current calendar date in the given IANA timezone.

    The streak "day" boundary must follow the user's wall clock, not UTC
    — otherwise a feed at 23:30 local could land on the wrong day. Falls
    back to UTC if ``tz`` is missing or not a recognised IANA zone.
    """
    if tz:
        try:
            return datetime.now(ZoneInfo(tz)).date()
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("streak: invalid timezone %r — falling back to UTC", tz)
    return datetime.now(UTC).date()


def _lock_streak_user(db: Session, user_id: uuid.UUID) -> None:
    """Serialise all streak mutations for one user within this transaction.

    ``SELECT ... FOR UPDATE`` only locks an existing ``user_streaks`` row
    — on the first-ever feed there is no row to lock, so two concurrent
    requests would both pass the same-day check and double-award. A
    transaction-scoped advisory lock keyed on the user_id closes that
    window regardless of row existence. Released automatically at
    commit/rollback.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": str(user_id)},
    )


# Re-export so callers can import exceptions from here or from exceptions.py
__all__ = [
    "FeedResult",
    "ReserveLimitExceeded",
    "StreakNeedsRepair",
    "StreakNotInRepairState",
    "feed_jack",
    "get_streak",
    "get_streak_multiplier",
    "purchase_reserve",
    "repair_streak",
]


class FeedResult(NamedTuple):
    """Return type of feed_jack() — separates public API state from internal flow control."""

    state: dict[str, Any]
    is_new_feed: bool  # True → caller should award XP; False → idempotent (already fed today)


def get_streak_multiplier(db: Session, user_id: uuid.UUID) -> float:
    """
    Return the active streak multiplier for the user (0.0 if no active streak today).

    Computed as: min(current_streak_days * 0.05, 1.0).
    A multiplier is only active when last_fed_at == today in the user's
    stored timezone (matching the feed_jack day boundary).

    Imported by cab_repository and xp_repository to apply the streak bonus on awards.
    """
    row = db.execute(
        text("SELECT current_streak_days, last_fed_at, timezone FROM user_streaks WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    if row is None or row.last_fed_at is None:
        return 0.0
    if row.last_fed_at != _today_in_tz(row.timezone):
        return 0.0
    return min(row.current_streak_days * 0.05, 1.0)


def get_streak(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """
    Return the full streak state for the user.

    Returns zeroed state if no streak row exists (lazy init on first feed).
    Never raises.
    """
    row = db.execute(
        text("SELECT current_streak_days, last_fed_at, food_reserves, timezone FROM user_streaks WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()

    if row is None:
        return _empty_streak_state()

    today = _today_in_tz(row.timezone)
    streak_days = row.current_streak_days
    last_fed_at = row.last_fed_at
    food_reserves = row.food_reserves

    already_fed_today = last_fed_at == today if last_fed_at else False

    # Compute gap_days to determine needs_repair
    if last_fed_at is None or already_fed_today:
        gap_days = 0
    else:
        gap_days = (today - last_fed_at).days - 1

    needs_repair = gap_days == 1 and food_reserves == 0 and streak_days > 0
    multiplier = min(streak_days * 0.05, 1.0) if streak_days > 0 else 0.0

    return {
        "streak_days": streak_days,
        "multiplier": round(multiplier, 4),
        "food_reserves": food_reserves,
        "already_fed_today": already_fed_today,
        "needs_repair": needs_repair,
        "frozen_days_used": 0,  # not tracked persistently — only meaningful on the feed call
        "timezone": row.timezone,
    }


def feed_jack(
    db: Session,
    user_id: uuid.UUID,
    xp_per_feed: int,  # kept for interface symmetry — XP is awarded by the caller
    tz_hint: str | None = None,
) -> FeedResult:
    """
    Feed Jack — core streak advancement logic.

    Returns FeedResult(state, is_new_feed).
      - state:       public API dict (streak_days, multiplier, …)
      - is_new_feed: True → caller should award XP; False → idempotent same-day call

    Raises StreakNeedsRepair when gap=1 and food_reserves=0.
    This function only mutates user_streaks — the caller commits.
    """
    # Serialise concurrent feeds for this user (advisory lock covers the
    # no-row-yet case ; FOR UPDATE locks an existing row). Without it two
    # same-day POSTs both pass the last_fed_at check → double XP.
    _lock_streak_user(db, user_id)
    row = db.execute(
        text(
            "SELECT current_streak_days, last_fed_at, food_reserves, timezone "
            "FROM user_streaks WHERE user_id = :uid "
            "FOR UPDATE"
        ),
        {"uid": user_id},
    ).first()

    if row is None:
        # First-ever feed — create the row
        tz = tz_hint or _DEFAULT_TZ
        today = _today_in_tz(tz)
        _upsert_streak(db, user_id, current_streak_days=1, last_fed_at=today, food_reserves=0, tz=tz)
        return FeedResult(state=_build_state(1, 0, today, frozen_days_used=0, tz=tz), is_new_feed=True)

    streak_days = row.current_streak_days
    last_fed_at = row.last_fed_at
    food_reserves = row.food_reserves
    tz = tz_hint if tz_hint else row.timezone
    today = _today_in_tz(tz)

    # Already fed today — idempotent
    if last_fed_at == today:
        return FeedResult(
            state=_build_state(streak_days, food_reserves, today, frozen_days_used=0, tz=tz, already_fed_today=True),
            is_new_feed=False,
        )

    # Compute gap
    gap_days = (today - last_fed_at).days - 1 if last_fed_at else 0

    frozen_days_used = 0

    if gap_days <= 0:
        new_streak = streak_days + 1
        new_reserves = food_reserves

    elif 0 < gap_days <= food_reserves:
        # Auto-freeze: consume gap_days reserves silently
        frozen_days_used = gap_days
        new_streak = streak_days + 1
        new_reserves = food_reserves - gap_days

    elif gap_days == 1 and food_reserves == 0 and streak_days > 0:
        raise StreakNeedsRepair("gap=1, no reserves — use /streak/repair")

    else:
        # gap > food_reserves OR gap >= 2 without coverage → reset
        new_streak = 1
        new_reserves = 0

    _upsert_streak(db, user_id, current_streak_days=new_streak, last_fed_at=today, food_reserves=new_reserves, tz=tz)
    return FeedResult(
        state=_build_state(new_streak, new_reserves, today, frozen_days_used=frozen_days_used, tz=tz),
        is_new_feed=True,
    )


def repair_streak(
    db: Session,
    user_id: uuid.UUID,
    repair_cost_cab: int,
) -> dict[str, Any]:
    """
    Repair a broken streak (gap=1, food_reserves=0) — costs repair_cost_cab CABs.

    Raises StreakNotInRepairState if not in repair state.
    Raises InsufficientBalance if balance too low.
    """
    # Serialise concurrent repairs — without the lock two requests both
    # pass the repair-state check and both debit CAB for one repair.
    _lock_streak_user(db, user_id)
    row = db.execute(
        text(
            "SELECT current_streak_days, last_fed_at, food_reserves, timezone "
            "FROM user_streaks WHERE user_id = :uid "
            "FOR UPDATE"
        ),
        {"uid": user_id},
    ).first()

    if row is None:
        raise StreakNotInRepairState("no streak row — nothing to repair")

    streak_days = row.current_streak_days
    last_fed_at = row.last_fed_at
    food_reserves = row.food_reserves
    tz = row.timezone
    today = _today_in_tz(tz)

    if last_fed_at is None:
        raise StreakNotInRepairState("streak never started")

    gap_days = (today - last_fed_at).days - 1
    if not (gap_days == 1 and food_reserves == 0 and streak_days > 0):
        raise StreakNotInRepairState(
            f"not in repair state (gap={gap_days}, reserves={food_reserves}, streak={streak_days})"
        )

    # Debit CABs inline (raw SQL — same atomicity pattern as cab_repository.debit_cab)
    _debit_cab(db, user_id, repair_cost_cab, reason="streak_repair")

    new_streak = streak_days + 1
    _upsert_streak(db, user_id, current_streak_days=new_streak, last_fed_at=today, food_reserves=0, tz=tz)
    return _build_state(new_streak, 0, today, frozen_days_used=0, tz=tz)


def purchase_reserve(
    db: Session,
    user_id: uuid.UUID,
    quantity: int,
    cost_per_reserve_cab: int,
    max_food_reserves: int,
) -> dict[str, Any]:
    """
    Purchase food reserves for Jack.

    Raises ValueError if quantity < 1.
    Raises ReserveLimitExceeded if current + quantity > max_food_reserves.
    Raises InsufficientBalance if balance too low.
    """
    if quantity < 1:
        raise ValueError("quantity must be >= 1")

    # Serialise concurrent purchases — without the lock two requests both
    # read the same food_reserves and the last-write-wins _upsert_streak
    # makes the user pay twice for one batch.
    _lock_streak_user(db, user_id)
    row = db.execute(
        text(
            "SELECT current_streak_days, last_fed_at, food_reserves, timezone "
            "FROM user_streaks WHERE user_id = :uid "
            "FOR UPDATE"
        ),
        {"uid": user_id},
    ).first()

    current_reserves = row.food_reserves if row else 0
    tz = row.timezone if row else _DEFAULT_TZ
    streak_days = row.current_streak_days if row else 0
    last_fed_at = row.last_fed_at if row else None

    if current_reserves + quantity > max_food_reserves:
        raise ReserveLimitExceeded(f"would exceed max_food_reserves ({max_food_reserves})")

    total_cost = cost_per_reserve_cab * quantity
    _debit_cab(db, user_id, total_cost, reason="food_reserve_purchase")

    new_reserves = current_reserves + quantity
    _upsert_streak(
        db,
        user_id,
        current_streak_days=streak_days,
        last_fed_at=last_fed_at,
        food_reserves=new_reserves,
        tz=tz,
    )
    return {
        "food_reserves": new_reserves,
        "cab_spent": total_cost,
        "streak_days": streak_days,
        "multiplier": round(min(streak_days * 0.05, 1.0), 4),
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _debit_cab(db: Session, user_id: uuid.UUID, amount: int, *, reason: str) -> None:
    """
    Atomic CAB debit — same logic as cab_repository.debit_cab.

    Defined inline to avoid a circular import (cab_repository imports
    get_streak_multiplier from this module).
    Raises InsufficientBalance if balance < amount.
    """
    result = db.execute(
        text("UPDATE user_cab_balance SET balance = balance - :amount WHERE user_id = :uid AND balance >= :amount"),
        {"amount": amount, "uid": user_id},
    )
    if affected_rows(result) == 0:
        raise InsufficientBalance("insufficient CAB balance")
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "    (id, user_id, direction, amount, reason) "
            "VALUES (:id, :uid, 'debit', :amount, :reason)"
        ),
        {"id": uuid.uuid4(), "uid": user_id, "amount": amount, "reason": reason},
    )


def _upsert_streak(
    db: Session,
    user_id: uuid.UUID,
    *,
    current_streak_days: int,
    last_fed_at: date | None,
    food_reserves: int,
    tz: str,
) -> None:
    db.execute(
        text(
            "INSERT INTO user_streaks "
            "    (user_id, current_streak_days, last_fed_at, food_reserves, timezone) "
            "VALUES (:uid, :days, :last_fed, :reserves, :tz) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "    current_streak_days = EXCLUDED.current_streak_days, "
            "    last_fed_at         = EXCLUDED.last_fed_at, "
            "    food_reserves       = EXCLUDED.food_reserves, "
            "    timezone            = EXCLUDED.timezone"
        ),
        {
            "uid": user_id,
            "days": current_streak_days,
            "last_fed": last_fed_at,
            "reserves": food_reserves,
            "tz": tz,
        },
    )


def _build_state(
    streak_days: int,
    food_reserves: int,
    last_fed_at: date,
    *,
    frozen_days_used: int,
    tz: str,
    already_fed_today: bool = False,
) -> dict[str, Any]:
    today = _today_in_tz(tz)
    multiplier = round(min(streak_days * 0.05, 1.0), 4)
    return {
        "streak_days": streak_days,
        "multiplier": multiplier,
        "food_reserves": food_reserves,
        "already_fed_today": already_fed_today or (last_fed_at == today),
        "needs_repair": False,
        "frozen_days_used": frozen_days_used,
        "timezone": tz,
    }


def _empty_streak_state() -> dict[str, Any]:
    return {
        "streak_days": 0,
        "multiplier": 0.0,
        "food_reserves": 0,
        "already_fed_today": False,
        "needs_repair": False,
        "frozen_days_used": 0,
        "timezone": _DEFAULT_TZ,
    }
