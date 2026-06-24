"""Admin BattlePass endpoints — seasons + milestones (tiers) administration.

Endpoints
=========

``GET  /admin/battlepass/seasons``
    Read-only listing of all seasons (active + inactive). ``ADMIN_API_KEY``
    only — no operator header.

``POST /admin/battlepass/seasons``
    Create a new season (``is_active=False`` by default — must be explicitly
    activated). Validates ``ends_at > started_at`` and unique ``season_number``.
    Requires ``ADMIN_API_KEY`` + ``X-Admin-Operator``.

``PATCH /admin/battlepass/seasons/{id}/activate``
    Set ``is_active=TRUE``. Enforces single-active invariant : 409 if any
    other season is already active. Idempotent on the target row.
    Requires ``ADMIN_API_KEY`` + ``X-Admin-Operator``.

``POST /admin/battlepass/seasons/{id}/tiers``
    Create a milestone (the schema name — exposed as ``tiers`` for the admin
    UI per the spec) inside the season. Validates uniqueness of
    ``milestone_number`` per season and a known ``reward_type``.
    Requires ``ADMIN_API_KEY`` + ``X-Admin-Operator``.

Auth model
----------
PR7 endpoints are gamification-config — no direct money mutation, so TOTP
is **not** required (cf. PROD_CHECKLIST.md:196-198 + brief). The
``X-Admin-Operator`` header is required on every mutation for audit traceability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from db_utils import db_transaction
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from repositories.battlepass_repository import (
    admin_activate_season,
    admin_create_milestone,
    admin_create_season,
    admin_get_season,
    admin_list_seasons,
)
from repositories.exceptions import (
    ActiveSeasonConflict,
    MilestoneNumberConflict,
    SeasonNotFound,
    SeasonNumberConflict,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SeasonCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    season_number: int = Field(ge=1)
    started_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def _validate_window(self) -> SeasonCreateRequest:
        if self.ends_at <= self.started_at:
            raise ValueError("ends_at_must_be_after_started_at")
        return self


class MilestoneCreateRequest(BaseModel):
    """Spec calls these "tiers" but the canonical schema is ``milestones``.

    ``tier_level`` (spec) maps to ``milestone_number`` (DB column).
    ``xp_required`` (spec) maps to ``cab_required`` — the BP economy is
    CAB-driven (cf. ``ARCH_battlepass.md`` and the ``battlepass_milestones``
    table ``cab_required`` column). Renaming surfaces here would diverge
    from the schema — we keep DB names server-side and document the alias.

    A single ``reward_value`` int + ``reward_type`` enum captures every
    current reward shape (cab, gift_card, skin) — reward_value is the
    quantitative value (cab amount, denomination cents, or skin id).
    """

    milestone_number: int = Field(ge=1)
    cab_required: int = Field(ge=0)
    reward_type: Literal["cab", "gift_card", "skin"]
    reward_value: int = Field(ge=0)
    subscriber_only: bool = False


# ---------------------------------------------------------------------------
# GET /admin/battlepass/seasons — read-only listing
# ---------------------------------------------------------------------------
@router.get(
    "/admin/battlepass/seasons",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_battlepass_seasons(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return every battlepass season ordered by season_number desc."""
    seasons = admin_list_seasons(db)
    return {"seasons": seasons, "total": len(seasons)}


# ---------------------------------------------------------------------------
# POST /admin/battlepass/seasons — create (draft, is_active=False)
# ---------------------------------------------------------------------------
@router.post(
    "/admin/battlepass/seasons",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_post_battlepass_season(
    body: SeasonCreateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a new battlepass season. Operator header required for audit.

    Errors :
        - 403 ``forbidden`` — wrong ADMIN_API_KEY (verify_admin_key)
        - 409 ``season_number_conflict`` — season_number already taken
        - 422 ``ends_at_must_be_after_started_at`` — invalid time window
    """
    # x_admin_operator is required header — used for audit trail (logs).
    # We trust upstream observability (RequestIDMiddleware + access logs)
    # to capture the operator handle ; no JSONB context table for BP yet.
    _ = x_admin_operator
    try:
        with db_transaction(db):
            sid = admin_create_season(
                db,
                name=body.name,
                season_number=body.season_number,
                started_at=body.started_at,
                ends_at=body.ends_at,
            )
    except SeasonNumberConflict:
        raise HTTPException(status_code=409, detail="season_number_conflict")
    season = admin_get_season(db, sid)
    return season  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PATCH /admin/battlepass/seasons/{id}/activate
# ---------------------------------------------------------------------------
@router.patch(
    "/admin/battlepass/seasons/{season_id}/activate",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_activate_battlepass_season(
    season_id: uuid.UUID,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Activate a season — at most one active at a time.

    Errors :
        - 403 ``forbidden``
        - 404 ``season_not_found``
        - 409 ``active_season_conflict`` — another season already active
    """
    _ = x_admin_operator
    try:
        with db_transaction(db):
            admin_activate_season(db, season_id)
    except SeasonNotFound:
        raise HTTPException(status_code=404, detail="season_not_found")
    except ActiveSeasonConflict:
        raise HTTPException(status_code=409, detail="active_season_conflict")
    return {"id": str(season_id), "is_active": True}


# ---------------------------------------------------------------------------
# POST /admin/battlepass/seasons/{id}/tiers — create milestone
# ---------------------------------------------------------------------------
@router.post(
    "/admin/battlepass/seasons/{season_id}/tiers",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def admin_post_battlepass_milestone(
    season_id: uuid.UUID,
    body: MilestoneCreateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a milestone (tier) for a season.

    Errors :
        - 403 ``forbidden``
        - 404 ``season_not_found``
        - 409 ``milestone_number_conflict`` — tier_level already used
    """
    _ = x_admin_operator
    try:
        with db_transaction(db):
            mid = admin_create_milestone(
                db,
                season_id=season_id,
                milestone_number=body.milestone_number,
                cab_required=body.cab_required,
                reward_type=body.reward_type,
                reward_value=body.reward_value,
                subscriber_only=body.subscriber_only,
            )
    except SeasonNotFound:
        raise HTTPException(status_code=404, detail="season_not_found")
    except MilestoneNumberConflict:
        raise HTTPException(status_code=409, detail="milestone_number_conflict")
    return {
        "id": str(mid),
        "season_id": str(season_id),
        "milestone_number": body.milestone_number,
        "cab_required": body.cab_required,
        "reward_type": body.reward_type,
        "reward_value": body.reward_value,
        "subscriber_only": body.subscriber_only,
    }
