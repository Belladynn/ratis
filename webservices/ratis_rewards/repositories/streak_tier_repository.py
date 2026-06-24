"""
Streak tier repository — raw SQL for the ``streak_tiers`` table.

The table stores a list of streak tier rows : ``days`` (UNIQUE) → ``multiplier``
(NUMERIC(4,2)) → ``label``. Used by the streak engine to compute boosted
rewards once a user reaches a threshold of consecutive days. The admin
CRUD here is the source-of-truth surface used by the Admin UI in PR3.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.exceptions import (
    StreakTierNotFound,
    StreakTierUniquenessConflict,
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "days": row.days,
        # Numeric is serialized as a string to avoid float drift —
        # ``Decimal(body["multiplier"])`` round-trips losslessly.
        "multiplier": str(row.multiplier),
        "label": row.label,
    }


def admin_list_streak_tiers(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return paginated streak_tiers rows + total count."""
    params: dict[str, Any] = {"lim": limit, "off": offset}
    total_row = db.execute(text("SELECT COUNT(*) AS n FROM streak_tiers")).first()
    total = int(total_row.n) if total_row else 0

    rows = db.execute(
        text("SELECT id, days, multiplier, label FROM streak_tiers ORDER BY days LIMIT :lim OFFSET :off"),
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def admin_get_streak_tier(db: Session, streak_tier_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT id, days, multiplier, label FROM streak_tiers WHERE id = :id"),
        {"id": streak_tier_id},
    ).first()
    if row is None:
        return None
    return _row_to_dict(row)


def admin_create_streak_tier(
    db: Session,
    *,
    days: int,
    multiplier: Decimal,
    label: str,
) -> uuid.UUID:
    """Insert a streak_tiers row. Caller commits.

    Raises :
        StreakTierUniquenessConflict — ``days`` already exists.
    """
    dup = db.execute(
        text("SELECT 1 FROM streak_tiers WHERE days = :d LIMIT 1"),
        {"d": days},
    ).first()
    if dup is not None:
        raise StreakTierUniquenessConflict()

    tier_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO streak_tiers (id, days, multiplier, label) VALUES (:id, :d, :m, :l)"),
        {"id": tier_id, "d": days, "m": multiplier, "l": label},
    )
    return tier_id


def admin_update_streak_tier(
    db: Session,
    streak_tier_id: uuid.UUID,
    *,
    fields: dict[str, Any],
) -> None:
    """Partial UPDATE of a streak_tiers row. Caller commits.

    Raises :
        StreakTierNotFound — id does not exist.
        StreakTierUniquenessConflict — new ``days`` collides.
    """
    if not fields:
        existing = db.execute(
            text("SELECT 1 FROM streak_tiers WHERE id = :id"),
            {"id": streak_tier_id},
        ).first()
        if existing is None:
            raise StreakTierNotFound()
        return

    current = db.execute(
        text("SELECT days FROM streak_tiers WHERE id = :id FOR UPDATE"),
        {"id": streak_tier_id},
    ).first()
    if current is None:
        raise StreakTierNotFound()

    new_days = fields.get("days", current.days)
    if new_days != current.days:
        dup = db.execute(
            text("SELECT 1 FROM streak_tiers WHERE days = :d AND id <> :id LIMIT 1"),
            {"d": new_days, "id": streak_tier_id},
        ).first()
        if dup is not None:
            raise StreakTierUniquenessConflict()

    db.execute(
        text(
            "UPDATE streak_tiers SET "
            "  days       = COALESCE(CAST(:days AS integer), days), "
            "  multiplier = COALESCE(CAST(:multiplier AS numeric), multiplier), "
            "  label      = COALESCE(CAST(:label AS text), label) "
            "WHERE id = :id"
        ),
        {
            "days": fields.get("days"),
            "multiplier": fields.get("multiplier"),
            "label": fields.get("label"),
            "id": streak_tier_id,
        },
    )


def admin_delete_streak_tier(db: Session, streak_tier_id: uuid.UUID) -> dict[str, Any]:
    """Hard delete a streak_tiers row. Caller commits.

    Returns the deleted row's snapshot for the audit log.

    Raises :
        StreakTierNotFound — id does not exist.
    """
    row = db.execute(
        text("SELECT id, days, multiplier, label FROM streak_tiers WHERE id = :id"),
        {"id": streak_tier_id},
    ).first()
    if row is None:
        raise StreakTierNotFound()
    snapshot = _row_to_dict(row)
    db.execute(
        text("DELETE FROM streak_tiers WHERE id = :id"),
        {"id": streak_tier_id},
    )
    return snapshot
