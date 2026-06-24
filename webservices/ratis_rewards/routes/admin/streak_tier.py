"""Admin StreakTier endpoints — gamification streak tier administration.

The ``streak_tiers`` table maps a ``days`` count (UNIQUE) to a CAB
``multiplier`` (NUMERIC(4,2)) and a ``label``. The streak engine looks up
the highest tier reached by a user's current streak and applies the
multiplier to the base CAB reward of the action. This CRUD surface lets
the operator tune the tier ladder from the Admin UI (PR3).

Endpoints
=========

``GET    /admin/rewards/streak-tiers``         — paginated list
``GET    /admin/rewards/streak-tiers/{id}``    — single row
``POST   /admin/rewards/streak-tiers``         — create
``PATCH  /admin/rewards/streak-tiers/{id}``    — partial update
``DELETE /admin/rewards/streak-tiers/{id}``    — hard delete + audit row

Auth model
----------
``ADMIN_API_KEY`` only — config, not money. Mutations require the
``X-Admin-Operator`` header for audit traceability.

DELETE rationale
----------------
Like ``reward_config``, the model has no ``is_active`` / ``is_archived``
column, so soft-delete is not available. We hard-delete and persist the
snapshot in a ``pipeline_audit_log`` row (phase='manual').
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from db_utils import db_transaction
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from repositories.exceptions import (
    StreakTierNotFound,
    StreakTierUniquenessConflict,
)
from services.admin.streak_tier_service import (
    create_streak_tier,
    delete_streak_tier,
    get_streak_tier,
    list_streak_tiers,
    update_streak_tier,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
# NUMERIC(4,2) → max value 99.99. Multiplier must stay ≥ 0 (a tier with
# multiplier=0 disables CAB; negative values are non-sensical and a CHECK
# would otherwise blow up at INSERT time with a 500).
class StreakTierCreateRequest(BaseModel):
    days: int = Field(ge=1, le=10_000)
    multiplier: Decimal = Field(ge=Decimal("0"), le=Decimal("99.99"))
    label: str = Field(min_length=1, max_length=128)


class StreakTierPatchRequest(BaseModel):
    """Partial update — every field optional ; ``model_fields_set`` used to
    distinguish "absent" from "explicit None"."""

    days: int | None = Field(default=None, ge=1, le=10_000)
    multiplier: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    label: str | None = Field(default=None, min_length=1, max_length=128)


# ---------------------------------------------------------------------------
# GET /admin/rewards/streak-tiers
# ---------------------------------------------------------------------------
@router.get(
    "/admin/rewards/streak-tiers",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_tiers(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated streak_tiers listing."""
    rows, total = list_streak_tiers(db, limit=limit, offset=offset)
    return {"tiers": rows, "total": total}


# ---------------------------------------------------------------------------
# GET /admin/rewards/streak-tiers/{id}
# ---------------------------------------------------------------------------
@router.get(
    "/admin/rewards/streak-tiers/{streak_tier_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_get_tier(
    streak_tier_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Fetch a single streak_tier by id."""
    row = get_streak_tier(db, streak_tier_id)
    if row is None:
        raise HTTPException(status_code=404, detail="streak_tier_not_found")
    return row


# ---------------------------------------------------------------------------
# POST /admin/rewards/streak-tiers
# ---------------------------------------------------------------------------
@router.post(
    "/admin/rewards/streak-tiers",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_tier(
    body: StreakTierCreateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a new streak_tier row.

    Errors :
        - 403 ``forbidden``
        - 409 ``streak_tier_uniqueness_conflict`` — ``days`` already exists
    """
    _ = x_admin_operator
    try:
        with db_transaction(db):
            tid = create_streak_tier(
                db,
                days=body.days,
                multiplier=body.multiplier,
                label=body.label,
            )
    except StreakTierUniquenessConflict:
        raise HTTPException(status_code=409, detail="streak_tier_uniqueness_conflict")
    tier = get_streak_tier(db, tid)
    return tier  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PATCH /admin/rewards/streak-tiers/{id}
# ---------------------------------------------------------------------------
@router.patch(
    "/admin/rewards/streak-tiers/{streak_tier_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_patch_tier(
    streak_tier_id: uuid.UUID,
    body: StreakTierPatchRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Partial update of a streak_tier row.

    Errors :
        - 403 ``forbidden``
        - 404 ``streak_tier_not_found``
        - 409 ``streak_tier_uniqueness_conflict``
    """
    _ = x_admin_operator
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        with db_transaction(db):
            update_streak_tier(db, streak_tier_id, fields=fields)
    except StreakTierNotFound:
        raise HTTPException(status_code=404, detail="streak_tier_not_found")
    except StreakTierUniquenessConflict:
        raise HTTPException(status_code=409, detail="streak_tier_uniqueness_conflict")
    tier = get_streak_tier(db, streak_tier_id)
    return tier  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# DELETE /admin/rewards/streak-tiers/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/admin/rewards/streak-tiers/{streak_tier_id}",
    status_code=204,
    dependencies=[Depends(verify_admin_key)],
)
def admin_delete_tier(
    streak_tier_id: uuid.UUID,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> Response:
    """Hard delete a streak_tier row + write a pipeline_audit_log entry.

    Errors :
        - 403 ``forbidden``
        - 404 ``streak_tier_not_found``
    """
    try:
        with db_transaction(db):
            delete_streak_tier(db, streak_tier_id, operator=x_admin_operator)
    except StreakTierNotFound:
        raise HTTPException(status_code=404, detail="streak_tier_not_found")
    return Response(status_code=204)
