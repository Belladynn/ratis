"""Admin RewardConfig endpoints — gamification CAB base amount per action.

The ``reward_config`` table maps an ``action_type`` (e.g. ``receipt_scan``)
to its base CAB reward amount. Used by the rewards engine when an event
is recorded ; this CRUD surface lets the operator tune the catalogue from
the Admin UI (PR3).

Endpoints
=========

``GET    /admin/rewards/configs``         — paginated list
``GET    /admin/rewards/configs/{id}``    — single row
``POST   /admin/rewards/configs``         — create
``PATCH  /admin/rewards/configs/{id}``    — partial update
``DELETE /admin/rewards/configs/{id}``    — hard delete + audit row

Auth model
----------
``ADMIN_API_KEY`` only — config, not money. Mutations require the
``X-Admin-Operator`` header for audit traceability (no TOTP — calque on
``routes/admin/missions.py`` and ``routes/admin/battlepass.py``).

DELETE rationale
----------------
The model has no ``is_active`` / ``is_archived`` column, so soft-delete
is not available. We hard-delete and persist the snapshot in a
``pipeline_audit_log`` row (phase='manual') for traceability — same
convention as the trust-score shadow-ban audit.
"""

from __future__ import annotations

import uuid
from typing import Any

from db_utils import db_transaction
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from repositories.exceptions import (
    RewardConfigNotFound,
    RewardConfigUniquenessConflict,
)
from services.admin.reward_config_service import (
    create_reward_config,
    delete_reward_config,
    get_reward_config,
    list_reward_configs,
    update_reward_config,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RewardConfigCreateRequest(BaseModel):
    action_type: str = Field(min_length=1, max_length=64)
    base_amount: int = Field(ge=0, le=1_000_000)


class RewardConfigPatchRequest(BaseModel):
    """Partial update — every field optional ; ``model_fields_set`` used to
    distinguish "absent" from "explicit None"."""

    action_type: str | None = Field(default=None, min_length=1, max_length=64)
    base_amount: int | None = Field(default=None, ge=0, le=1_000_000)


# ---------------------------------------------------------------------------
# GET /admin/rewards/configs
# ---------------------------------------------------------------------------
@router.get(
    "/admin/rewards/configs",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_configs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated reward_config listing."""
    rows, total = list_reward_configs(db, limit=limit, offset=offset)
    return {"configs": rows, "total": total}


# ---------------------------------------------------------------------------
# GET /admin/rewards/configs/{id}
# ---------------------------------------------------------------------------
@router.get(
    "/admin/rewards/configs/{reward_config_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_get_config(
    reward_config_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Fetch a single reward_config by id."""
    row = get_reward_config(db, reward_config_id)
    if row is None:
        raise HTTPException(status_code=404, detail="reward_config_not_found")
    return row


# ---------------------------------------------------------------------------
# POST /admin/rewards/configs
# ---------------------------------------------------------------------------
@router.post(
    "/admin/rewards/configs",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_config(
    body: RewardConfigCreateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a new reward_config row.

    Errors :
        - 403 ``forbidden`` — wrong ADMIN_API_KEY
        - 409 ``reward_config_uniqueness_conflict`` — action_type already exists
    """
    _ = x_admin_operator
    try:
        with db_transaction(db):
            rc_id = create_reward_config(
                db,
                action_type=body.action_type,
                base_amount=body.base_amount,
            )
    except RewardConfigUniquenessConflict:
        raise HTTPException(status_code=409, detail="reward_config_uniqueness_conflict")
    config = get_reward_config(db, rc_id)
    return config  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PATCH /admin/rewards/configs/{id}
# ---------------------------------------------------------------------------
@router.patch(
    "/admin/rewards/configs/{reward_config_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_patch_config(
    reward_config_id: uuid.UUID,
    body: RewardConfigPatchRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Partial update of a reward_config row.

    Errors :
        - 403 ``forbidden``
        - 404 ``reward_config_not_found``
        - 409 ``reward_config_uniqueness_conflict``
    """
    _ = x_admin_operator
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        with db_transaction(db):
            update_reward_config(db, reward_config_id, fields=fields)
    except RewardConfigNotFound:
        raise HTTPException(status_code=404, detail="reward_config_not_found")
    except RewardConfigUniquenessConflict:
        raise HTTPException(status_code=409, detail="reward_config_uniqueness_conflict")
    config = get_reward_config(db, reward_config_id)
    return config  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# DELETE /admin/rewards/configs/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/admin/rewards/configs/{reward_config_id}",
    status_code=204,
    dependencies=[Depends(verify_admin_key)],
)
def admin_delete_config(
    reward_config_id: uuid.UUID,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> Response:
    """Hard delete a reward_config row + write a pipeline_audit_log entry.

    Errors :
        - 403 ``forbidden``
        - 404 ``reward_config_not_found``
    """
    try:
        with db_transaction(db):
            delete_reward_config(db, reward_config_id, operator=x_admin_operator)
    except RewardConfigNotFound:
        raise HTTPException(status_code=404, detail="reward_config_not_found")
    return Response(status_code=204)
