"""Admin Missions endpoints — mission catalogue (templates) administration.

The brief uses the term "mission templates" — that's the canonical
``missions`` table (catalogue rows that the daily/weekly batch instantiates
into per-user ``user_missions`` rows). The actual schema is more constrained
than a generic ``requirements/rewards JSONB`` blob :

    action_type   ∈ {receipt_scan, label_scan, barcode_scan, price_compared}
    frequency     ∈ {daily, weekly}
    difficulty    ∈ {easy, medium, hard}
    target_count  > 0  (count of action_type events to clear the mission)
    cab_reward    ≥ 0  (reward when claimed)
    is_active     bool
    is_boostable  bool

We expose those columns as the template "shape". The brief mentions
``type=event`` but the schema's ``frequency`` CHECK constraint is
``daily|weekly`` only — adding ``event`` would require a migration we don't
own here. Spec-vs-schema mismatch flagged in NOTES of the report-back.

Endpoints
=========

``GET   /admin/missions/templates``  — list with optional filters
``POST  /admin/missions/templates``  — create new catalogue row
``PATCH /admin/missions/templates/{id}`` — partial update

Auth model
----------
Read-only listing : ``ADMIN_API_KEY`` only.
Mutations : ``ADMIN_API_KEY`` + ``X-Admin-Operator`` (no TOTP — config, not money).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from repositories.exceptions import (
    MissionNotFound,
    MissionUniquenessConflict,
)
from repositories.missions_repository import (
    admin_create_mission,
    admin_get_mission,
    admin_list_missions,
    admin_update_mission,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
ActionType = Literal["receipt_scan", "label_scan", "barcode_scan", "price_compared"]
Frequency = Literal["daily", "weekly"]
Difficulty = Literal["easy", "medium", "hard"]


class MissionTemplateCreateRequest(BaseModel):
    action_type: ActionType
    frequency: Frequency
    difficulty: Difficulty
    target_count: int = Field(ge=1, le=10_000)
    cab_reward: int = Field(ge=0, le=1_000_000)
    is_active: bool = True
    is_boostable: bool = True


class MissionTemplatePatchRequest(BaseModel):
    """Partial update — every field optional ; ``model_fields_set`` used to
    distinguish "absent" from "explicit None"."""

    action_type: ActionType | None = None
    frequency: Frequency | None = None
    difficulty: Difficulty | None = None
    target_count: int | None = Field(default=None, ge=1, le=10_000)
    cab_reward: int | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None
    is_boostable: bool | None = None


# ---------------------------------------------------------------------------
# GET /admin/missions/templates
# ---------------------------------------------------------------------------
@router.get(
    "/admin/missions/templates",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_mission_templates(
    frequency: Frequency | None = None,
    active: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated mission catalogue listing.

    Query params (all optional) :
        - ``frequency`` : daily | weekly
        - ``active`` : true | false (filter on ``is_active``)
        - ``limit`` (1..500, default 100), ``offset`` (default 0)
    """
    rows, total = admin_list_missions(
        db,
        frequency=frequency,
        is_active=active,
        limit=limit,
        offset=offset,
    )
    return {"templates": rows, "total": total}


# ---------------------------------------------------------------------------
# POST /admin/missions/templates
# ---------------------------------------------------------------------------
@router.post(
    "/admin/missions/templates",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_mission_template(
    body: MissionTemplateCreateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a new mission catalogue row.

    Errors :
        - 403 ``forbidden`` — wrong ADMIN_API_KEY
        - 409 ``mission_uniqueness_conflict`` — (action_type, frequency,
          difficulty) already exists
    """
    _ = x_admin_operator
    try:
        with db_transaction(db):
            mid = admin_create_mission(
                db,
                action_type=body.action_type,
                frequency=body.frequency,
                difficulty=body.difficulty,
                target_count=body.target_count,
                cab_reward=body.cab_reward,
                is_active=body.is_active,
                is_boostable=body.is_boostable,
            )
    except MissionUniquenessConflict:
        raise HTTPException(status_code=409, detail="mission_uniqueness_conflict")
    mission = admin_get_mission(db, mid)
    return mission  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PATCH /admin/missions/templates/{id}
# ---------------------------------------------------------------------------
@router.patch(
    "/admin/missions/templates/{mission_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_patch_mission_template(
    mission_id: uuid.UUID,
    body: MissionTemplatePatchRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Partial update of a mission catalogue row.

    Only fields present in the request body are written (``model_fields_set``
    distinguishes "absent" from "explicit None"). An empty body is a no-op
    that still confirms the mission exists (returns 200 with current state).

    Errors :
        - 403 ``forbidden``
        - 404 ``mission_not_found``
        - 409 ``mission_uniqueness_conflict`` — change collides with another row
    """
    _ = x_admin_operator
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        with db_transaction(db):
            admin_update_mission(db, mission_id, fields=fields)
    except MissionNotFound:
        raise HTTPException(status_code=404, detail="mission_not_found")
    except MissionUniquenessConflict:
        raise HTTPException(status_code=409, detail="mission_uniqueness_conflict")
    mission = admin_get_mission(db, mission_id)
    return mission  # type: ignore[return-value]
