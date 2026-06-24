"""Admin streak_tier service — wraps the repository CRUD with audit
emission for destructive operations.

Mirrors ``services/admin/reward_config_service.py`` — DELETE writes a
``pipeline_audit_log`` row (phase='manual') with the deleted snapshot so
the legal trail keeps the data even after the source row is gone.

Caller (route handler) is responsible for ``db.commit()`` (R02 — services
do not commit).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from repositories.streak_tier_repository import (
    admin_create_streak_tier as _repo_create,
)
from repositories.streak_tier_repository import (
    admin_delete_streak_tier as _repo_delete,
)
from repositories.streak_tier_repository import (
    admin_get_streak_tier as _repo_get,
)
from repositories.streak_tier_repository import (
    admin_list_streak_tiers as _repo_list,
)
from repositories.streak_tier_repository import (
    admin_update_streak_tier as _repo_update,
)
from sqlalchemy import text
from sqlalchemy.orm import Session


def list_streak_tiers(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return _repo_list(db, limit=limit, offset=offset)


def get_streak_tier(db: Session, streak_tier_id: uuid.UUID) -> dict[str, Any] | None:
    return _repo_get(db, streak_tier_id)


def create_streak_tier(
    db: Session,
    *,
    days: int,
    multiplier: Decimal,
    label: str,
) -> uuid.UUID:
    return _repo_create(db, days=days, multiplier=multiplier, label=label)


def update_streak_tier(
    db: Session,
    streak_tier_id: uuid.UUID,
    *,
    fields: dict[str, Any],
) -> None:
    _repo_update(db, streak_tier_id, fields=fields)


def delete_streak_tier(
    db: Session,
    streak_tier_id: uuid.UUID,
    *,
    operator: str,
) -> None:
    """Hard delete + write audit row."""
    snapshot = _repo_delete(db, streak_tier_id)
    payload = {
        "event": "streak_tier_deleted",
        "streak_tier_id": snapshot["id"],
        "days": snapshot["days"],
        "multiplier": snapshot["multiplier"],
        "label": snapshot["label"],
        "operator": operator,
    }
    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "    (phase, level, event, scan_id, payload, created_at) "
            "VALUES "
            "    ('manual', 'normal', 'streak_tier_deleted', "
            "     NULL, CAST(:payload AS jsonb), clock_timestamp())"
        ),
        {"payload": json.dumps(payload)},
    )
