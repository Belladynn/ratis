"""Admin Achievements catalog endpoints — PR 6/8 of Achievements V1.

Five endpoints, all gated by ``ADMIN_API_KEY`` ; mutating endpoints
also require the ``X-Admin-Operator`` header (honor-system handle
stamped into every audit row).

============  ================================================  =================
Method+Path   Purpose                                            Audit event
============  ================================================  =================
GET    /admin/achievements                                       — (read-only)
POST   /admin/achievements                                       achievement_created
PATCH  /admin/achievements/{id}                                  achievement_updated
DELETE /admin/achievements/{id}                                  achievement_deleted
POST   /admin/users/{uid}/achievements/{aid}/grant               achievement_admin_granted
============  ================================================  =================

Auth model
----------
- ``ADMIN_API_KEY`` (Bearer) — required on every endpoint, validated by
  ``ratis_core.deps.verify_admin_key`` (constant-time compare ; 403 on
  failure ; never leaks config state).
- ``X-Admin-Operator`` (Header) — required on every mutating endpoint
  (POST, PATCH, DELETE, grant). Honor-system handle (no crypto), only
  used to stamp the audit log so the operator who acted is traceable.
- Read-only ``GET`` does NOT require ``X-Admin-Operator`` (read-only,
  no audit row to stamp).

Audit log
---------
Every mutating endpoint writes a row to ``pipeline_audit_log`` with
``phase='manual'`` (mirrors the convention from
``routes/admin/trust_scores.py`` and ``services/admin/streak_tier_service.py``
— ``manual`` is one of the values in the ``ck_pipeline_audit_log_phase``
CHECK enum).

Immutable-after-unlock guard
----------------------------
Once at least one user has unlocked an achievement, the fields that
shape the unlock condition or the prize become read-only :

    {trigger_type, target_value, window_days, extra_params, rarity, cab_reward}

Touching any of them returns 409 ``achievement_immutable_after_unlock``.
Cosmetic fields (label, description, icon, display_order, etc.)
remain editable so we can fix typos / illustrations without
risking stale snapshots in ``user_achievements`` (the row already
holds ``cab_granted`` at unlock time — re-pricing the catalog never
rewrites historical grants, see ``user_achievements.cab_granted``).

Force-unlock semantics
----------------------
The manual-grant endpoint reuses ``services.achievement_service._unlock``
so the side effects (CAB credit via canonical ``award_cab``, JSONB
trigger_event truncation, ``user_achievements`` insert) are exactly
identical to an event-driven unlock. Idempotent : re-granting an
already-unlocked achievement is a no-op (no double CAB grant). The
response carries a ``previous: bool`` so the operator knows whether
the call had a real effect.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.models.achievement import Achievement, UserAchievement
from ratis_core.models.user import User
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IMMUTABLE_AFTER_UNLOCK: frozenset[str] = frozenset(
    {
        "trigger_type",
        "target_value",
        "window_days",
        "extra_params",
        "rarity",
        "cab_reward",
    }
)


# All Achievement columns that an admin is allowed to PATCH. Anything
# else (id, created_at, updated_at, code) is silently dropped so a
# typo or a malicious payload can't smuggle a mutation past the guard.
PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {
        "label",
        "description",
        "icon",
        "rarity",
        "category",
        "trigger_type",
        "target_value",
        "window_days",
        "extra_params",
        "cab_reward",
        "is_secret",
        "is_hidden",
        "available_from",
        "available_until",
        "display_order",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _audit_log(
    db: Session,
    *,
    event: str,
    operator: str,
    payload: dict[str, Any],
) -> None:
    """Insert a ``pipeline_audit_log`` row stamped ``phase='manual'``.

    Mirrors the convention used by ``routes/admin/trust_scores.py``
    and ``services/admin/streak_tier_service.py``. The caller is
    responsible for ``db.commit()`` — services never commit on their
    own (R02).
    """
    full_payload = {"event": event, "operator": operator, **payload}
    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "    (phase, level, event, scan_id, payload, created_at) "
            "VALUES "
            "    ('manual', 'normal', :event, NULL, "
            "     CAST(:payload AS jsonb), clock_timestamp())"
        ),
        {"event": event, "payload": json.dumps(full_payload, default=str)},
    )


def _has_unlocks(db: Session, achievement_id: uuid.UUID) -> bool:
    cnt = db.scalar(
        select(func.count()).select_from(UserAchievement).where(UserAchievement.achievement_id == achievement_id)
    )
    return (cnt or 0) > 0


def _serialize_admin(ach: Achievement, unlocked_users: int, total_users: int) -> dict[str, Any]:
    """Admin-facing serialization — no masking, full catalog state."""
    pct = (unlocked_users / total_users * 100) if total_users > 0 else 0.0
    return {
        "id": str(ach.id),
        "code": ach.code,
        "label": ach.label,
        "description": ach.description,
        "icon": ach.icon,
        "rarity": ach.rarity,
        "category": ach.category,
        "trigger_type": ach.trigger_type,
        "target_value": float(ach.target_value),
        "window_days": ach.window_days,
        "extra_params": ach.extra_params,
        "cab_reward": ach.cab_reward,
        "is_secret": ach.is_secret,
        "is_hidden": ach.is_hidden,
        "available_from": (ach.available_from.isoformat() if ach.available_from else None),
        "available_until": (ach.available_until.isoformat() if ach.available_until else None),
        "display_order": ach.display_order,
        "unlocked_users": unlocked_users,
        "unlock_percentage": round(pct, 1),
        "created_at": ach.created_at.isoformat() if ach.created_at else None,
    }


# ---------------------------------------------------------------------------
# Pydantic bodies
# ---------------------------------------------------------------------------
class CreateAchievementBody(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1)
    icon: str = Field(min_length=1, max_length=64)
    rarity: str
    category: str
    trigger_type: str
    target_value: float = Field(gt=0)
    window_days: int | None = Field(default=None, gt=0)
    extra_params: dict[str, Any] | None = None
    cab_reward: int = Field(ge=0)
    is_secret: bool = False
    is_hidden: bool = False
    available_from: datetime | None = None
    available_until: datetime | None = None
    display_order: int = 0


class GrantBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------------------
# GET /admin/achievements
# ---------------------------------------------------------------------------
@router.get(
    "/admin/achievements",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def list_admin_achievements(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return every catalog row + unlock stats.

    No filters / pagination in V1 — the catalog is bounded (~30-50
    entries even at maturity) so emitting them all in one shot keeps
    the admin UI simple. If the catalog ever grows past 200 we can
    add ``limit/offset`` then.

    Stats per row :
    - ``unlocked_users`` — distinct users who have a row in
      ``user_achievements`` for this achievement.
    - ``unlock_percentage`` — ``unlocked_users / total_active_users * 100``,
      rounded to 1 decimal. Total = users with ``is_deleted=false``
      (matching the user-base cohort the achievement is offered to).
    """
    total_users = db.scalar(select(func.count()).select_from(User).where(User.is_deleted.is_(False))) or 0

    achievements = db.scalars(
        select(Achievement).order_by(Achievement.display_order.asc(), Achievement.created_at.desc())
    ).all()

    out: list[dict[str, Any]] = []
    for ach in achievements:
        unlocked_users = (
            db.scalar(select(func.count()).select_from(UserAchievement).where(UserAchievement.achievement_id == ach.id))
            or 0
        )
        out.append(_serialize_admin(ach, unlocked_users, total_users))

    return {"total": len(out), "achievements": out}


# ---------------------------------------------------------------------------
# POST /admin/achievements
# ---------------------------------------------------------------------------
@router.post(
    "/admin/achievements",
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def create_admin_achievement(
    body: CreateAchievementBody,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Insert a new achievement + emit ``achievement_created`` audit row.

    Errors :
        - 422 ``cannot_create_jyetais_in_catalog`` — ``j_y_etais`` is
          a *display* category, never a stored one (cf. CHECK constraint
          ``ck_achievements_no_jyetais_in_catalog`` in the migration).
          Limited-time achievements are seeded with their real category
          and surfaced as ``j_y_etais`` only at serialize time when the
          window is closed (cf. ``services/achievement_serializer.py``).
        - 409 ``achievement_code_taken`` — UNIQUE(code) violated.
        - 422 (Pydantic) — body validation errors.
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY.
    """
    if body.category == "j_y_etais":
        raise HTTPException(
            status_code=422,
            detail="cannot_create_jyetais_in_catalog",
        )

    ach = Achievement(**body.model_dump())
    try:
        db.add(ach)
        db.flush()  # surface IntegrityError before commit
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig) if e.orig else str(e)
        if "achievements_code_key" in msg or "uq_achievements_code" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="achievement_code_taken",
            )
        raise

    _audit_log(
        db,
        event="achievement_created",
        operator=x_admin_operator,
        payload={
            "achievement_id": str(ach.id),
            "code": ach.code,
            "rarity": ach.rarity,
            "category": ach.category,
            "trigger_type": ach.trigger_type,
        },
    )
    db.commit()

    return {"id": str(ach.id), "code": ach.code}


# ---------------------------------------------------------------------------
# PATCH /admin/achievements/{achievement_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/admin/achievements/{achievement_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def patch_admin_achievement(
    achievement_id: uuid.UUID,
    body: dict[str, Any],
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Partial update with the immutable-after-unlock guard.

    Body is a free-form dict (we filter against ``PATCHABLE_FIELDS``
    so unknown / forbidden keys are silently dropped — no surprise
    mutations on ``id`` / ``code`` / ``created_at``).

    Errors :
        - 404 ``achievement_not_found``
        - 409 ``achievement_immutable_after_unlock`` — the achievement
          has at least one row in ``user_achievements`` and the body
          touches one of ``IMMUTABLE_AFTER_UNLOCK``.
        - 403 ``forbidden``.
    """
    ach = db.get(Achievement, achievement_id)
    if ach is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="achievement_not_found",
        )

    requested = {k: v for k, v in body.items() if k in PATCHABLE_FIELDS}

    if requested:
        bad = set(requested.keys()) & IMMUTABLE_AFTER_UNLOCK
        if bad and _has_unlocks(db, ach.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="achievement_immutable_after_unlock",
            )

    for k, v in requested.items():
        setattr(ach, k, v)
    # Force the UPDATE to land before the audit_log INSERT so the
    # commit sequence is fully observable (no implicit autoflush on
    # commit() — the test infra's tracking guard relies on writes
    # being visible BEFORE commit clears them).
    db.flush()

    _audit_log(
        db,
        event="achievement_updated",
        operator=x_admin_operator,
        payload={
            "achievement_id": str(ach.id),
            "code": ach.code,
            "fields": sorted(requested.keys()),
        },
    )
    db.commit()

    return {
        "id": str(ach.id),
        "updated_fields": sorted(requested.keys()),
    }


# ---------------------------------------------------------------------------
# DELETE /admin/achievements/{achievement_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/admin/achievements/{achievement_id}",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def delete_admin_achievement(
    achievement_id: uuid.UUID,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Hard delete (only allowed if 0 unlocks).

    The ``user_achievements.achievement_id`` FK is ``ON DELETE
    RESTRICT``, so we check first and return a clean 409 instead of
    letting the FK violation bubble up. Once an achievement has been
    unlocked it is part of users' history — deletion would orphan the
    snapshot (``cab_granted`` is preserved in the user row, but the
    catalog metadata used by the UI to render the unlock would
    vanish).

    Errors :
        - 404 ``achievement_not_found``
        - 409 ``achievement_has_unlocks``
        - 403 ``forbidden``.
    """
    ach = db.get(Achievement, achievement_id)
    if ach is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="achievement_not_found",
        )

    if _has_unlocks(db, ach.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="achievement_has_unlocks",
        )

    deleted_code = ach.code
    db.delete(ach)
    # Force the DELETE to land before the audit_log INSERT — same
    # rationale as the PATCH route (commit() does not autoflush
    # cleanly relative to the tracking guard).
    db.flush()
    _audit_log(
        db,
        event="achievement_deleted",
        operator=x_admin_operator,
        payload={
            "achievement_id": str(achievement_id),
            "code": deleted_code,
        },
    )
    db.commit()

    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/achievements/{achievement_id}/grant
# ---------------------------------------------------------------------------
@router.post(
    "/admin/users/{user_id}/achievements/{achievement_id}/grant",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_grant_achievement(
    user_id: uuid.UUID,
    achievement_id: uuid.UUID,
    body: GrantBody,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Force-unlock an achievement for a user (idempotent).

    Reuses ``services.achievement_service._unlock`` so the side-effects
    (CAB credit via ``award_cab``, JSONB trigger_event truncation,
    UNIQUE-protected insert) are byte-identical to an event-driven
    unlock. The trigger_event payload carries ``source='admin_manual'``
    so analytics can split admin-granted from organic unlocks.

    Idempotent : re-granting an already-unlocked achievement is a
    no-op (``ON CONFLICT DO NOTHING`` inside ``_unlock``). The
    response always carries ``previous: bool`` so the caller can
    distinguish a real grant from a replay.

    Errors :
        - 404 ``achievement_not_found``
        - 422 — invalid reason (Pydantic min_length=3 / max_length=500)
        - 403 ``forbidden``.
    """
    ach = db.get(Achievement, achievement_id)
    if ach is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="achievement_not_found",
        )

    # Local import — avoids a hard dependency at module load (the
    # service itself imports from repositories.cab_repository which
    # has its own transitive deps).
    from services.achievement_service import _unlock

    previous = _has_unlocks_for_user(db, user_id, achievement_id)
    if not previous:
        _unlock(
            db,
            user_id,
            ach,
            trigger_event={
                "source": "admin_manual",
                "operator": x_admin_operator,
                "reason": body.reason,
            },
        )
        # Since F-RW-1 ``_unlock`` no longer commits — the explicit
        # ``db.commit()`` below persists the unlock atomically with the
        # audit row, so a forensic trail is impossible to lose.

    _audit_log(
        db,
        event="achievement_admin_granted",
        operator=x_admin_operator,
        payload={
            "user_id": str(user_id),
            "achievement_id": str(achievement_id),
            "code": ach.code,
            "reason": body.reason,
            "previous": previous,
        },
    )
    db.commit()

    return {
        "user_id": str(user_id),
        "achievement_id": str(achievement_id),
        "previous": previous,
    }


def _has_unlocks_for_user(db: Session, user_id: uuid.UUID, achievement_id: uuid.UUID) -> bool:
    cnt = db.scalar(
        select(func.count())
        .select_from(UserAchievement)
        .where(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement_id == achievement_id,
        )
    )
    return (cnt or 0) > 0
