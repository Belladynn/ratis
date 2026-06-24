"""
Reward config repository — raw SQL for the ``reward_config`` table.

The table maps an ``action_type`` (e.g. ``receipt_scan``) to its base CAB
reward amount. The catalogue is consumed by the rewards engine when an
event is recorded ; the admin CRUD here is the source-of-truth surface
used by the Admin UI in PR3.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.exceptions import (
    RewardConfigNotFound,
    RewardConfigUniquenessConflict,
)


def admin_list_reward_configs(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return paginated reward_config rows + total count."""
    params: dict[str, Any] = {"lim": limit, "off": offset}
    total_row = db.execute(text("SELECT COUNT(*) AS n FROM reward_config")).first()
    total = int(total_row.n) if total_row else 0

    rows = db.execute(
        text("SELECT id, action_type, base_amount FROM reward_config ORDER BY action_type LIMIT :lim OFFSET :off"),
        params,
    ).fetchall()
    return (
        [
            {
                "id": str(r.id),
                "action_type": r.action_type,
                "base_amount": r.base_amount,
            }
            for r in rows
        ],
        total,
    )


def admin_get_reward_config(db: Session, reward_config_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT id, action_type, base_amount FROM reward_config WHERE id = :id"),
        {"id": reward_config_id},
    ).first()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "action_type": row.action_type,
        "base_amount": row.base_amount,
    }


def admin_create_reward_config(
    db: Session,
    *,
    action_type: str,
    base_amount: int,
) -> uuid.UUID:
    """Insert a reward_config row. Caller commits.

    Raises :
        RewardConfigUniquenessConflict — ``action_type`` already exists.
    """
    dup = db.execute(
        text("SELECT 1 FROM reward_config WHERE action_type = :a LIMIT 1"),
        {"a": action_type},
    ).first()
    if dup is not None:
        raise RewardConfigUniquenessConflict()

    rc_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO reward_config (id, action_type, base_amount) VALUES (:id, :a, :b)"),
        {"id": rc_id, "a": action_type, "b": base_amount},
    )
    return rc_id


def admin_update_reward_config(
    db: Session,
    reward_config_id: uuid.UUID,
    *,
    fields: dict[str, Any],
) -> None:
    """Partial UPDATE of a reward_config row. Caller commits.

    Raises :
        RewardConfigNotFound — id does not exist.
        RewardConfigUniquenessConflict — new action_type collides.
    """
    if not fields:
        existing = db.execute(
            text("SELECT 1 FROM reward_config WHERE id = :id"),
            {"id": reward_config_id},
        ).first()
        if existing is None:
            raise RewardConfigNotFound()
        return

    current = db.execute(
        text("SELECT action_type FROM reward_config WHERE id = :id FOR UPDATE"),
        {"id": reward_config_id},
    ).first()
    if current is None:
        raise RewardConfigNotFound()

    new_action = fields.get("action_type", current.action_type)
    if new_action != current.action_type:
        dup = db.execute(
            text("SELECT 1 FROM reward_config WHERE action_type = :a AND id <> :id LIMIT 1"),
            {"a": new_action, "id": reward_config_id},
        ).first()
        if dup is not None:
            raise RewardConfigUniquenessConflict()

    db.execute(
        text(
            "UPDATE reward_config SET "
            "  action_type = COALESCE(CAST(:action_type AS text), action_type), "
            "  base_amount = COALESCE(CAST(:base_amount AS integer), base_amount) "
            "WHERE id = :id"
        ),
        {
            "action_type": fields.get("action_type"),
            "base_amount": fields.get("base_amount"),
            "id": reward_config_id,
        },
    )


def admin_delete_reward_config(db: Session, reward_config_id: uuid.UUID) -> dict[str, Any]:
    """Hard delete a reward_config row. Caller commits.

    Returns the deleted row's snapshot (id, action_type, base_amount) so the
    caller can stamp it into an audit row.

    Raises :
        RewardConfigNotFound — id does not exist.
    """
    row = db.execute(
        text("SELECT id, action_type, base_amount FROM reward_config WHERE id = :id"),
        {"id": reward_config_id},
    ).first()
    if row is None:
        raise RewardConfigNotFound()
    snapshot = {
        "id": str(row.id),
        "action_type": row.action_type,
        "base_amount": row.base_amount,
    }
    db.execute(
        text("DELETE FROM reward_config WHERE id = :id"),
        {"id": reward_config_id},
    )
    return snapshot
