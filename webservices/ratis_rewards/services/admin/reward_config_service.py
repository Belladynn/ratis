"""Admin reward_config service — wraps the repository CRUD with audit
emission for destructive operations.

The service layer keeps two responsibilities :

1. Translate the route layer's domain payload into repository calls.
2. Emit a ``pipeline_audit_log`` row (phase='manual') on DELETE so the
   destructive op is traceable, mirroring the ``user_shadow_ban_changed``
   pattern in ``routes/admin/trust_scores.py``.

Caller (route handler) is responsible for ``db.commit()`` (R02 — services
do not commit).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from repositories.reward_config_repository import (
    admin_create_reward_config as _repo_create,
)
from repositories.reward_config_repository import (
    admin_delete_reward_config as _repo_delete,
)
from repositories.reward_config_repository import (
    admin_get_reward_config as _repo_get,
)
from repositories.reward_config_repository import (
    admin_list_reward_configs as _repo_list,
)
from repositories.reward_config_repository import (
    admin_update_reward_config as _repo_update,
)
from sqlalchemy import text
from sqlalchemy.orm import Session


def list_reward_configs(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    return _repo_list(db, limit=limit, offset=offset)


def get_reward_config(db: Session, reward_config_id: uuid.UUID) -> dict[str, Any] | None:
    return _repo_get(db, reward_config_id)


def create_reward_config(
    db: Session,
    *,
    action_type: str,
    base_amount: int,
) -> uuid.UUID:
    return _repo_create(db, action_type=action_type, base_amount=base_amount)


def update_reward_config(
    db: Session,
    reward_config_id: uuid.UUID,
    *,
    fields: dict[str, Any],
) -> None:
    _repo_update(db, reward_config_id, fields=fields)


def delete_reward_config(
    db: Session,
    reward_config_id: uuid.UUID,
    *,
    operator: str,
) -> None:
    """Hard delete + write audit row.

    The audit row carries the deleted snapshot (action_type, base_amount)
    so the legal trail still has the data even though the source row is
    gone.
    """
    snapshot = _repo_delete(db, reward_config_id)
    payload = {
        "event": "reward_config_deleted",
        "reward_config_id": snapshot["id"],
        "action_type": snapshot["action_type"],
        "base_amount": snapshot["base_amount"],
        "operator": operator,
    }
    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "    (phase, level, event, scan_id, payload, created_at) "
            "VALUES "
            "    ('manual', 'normal', 'reward_config_deleted', "
            "     NULL, CAST(:payload AS jsonb), clock_timestamp())"
        ),
        {"payload": json.dumps(payload)},
    )
