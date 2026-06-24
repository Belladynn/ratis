"""Admin trust-score endpoints — anti-fraud V1.

Exposes :

- ``GET /admin/trust-scores`` — paginated, filterable list of users with
  their batch-computed trust score. Used by the admin UI to triage
  warnings and shadow-banned accounts.
- ``PATCH /admin/users/{user_id}/shadow-ban`` — toggle ``is_shadow_banned``
  manually. Lets an operator (a) un-ban a false-positive set by the
  nightly batch or (b) hard-ban a confirmed bad actor before the next
  batch run.

Both endpoints are gated by ``ADMIN_API_KEY``. The PATCH also requires
the ``X-Admin-Operator`` header (stamped into the audit row context) —
mirrors the convention from ``admin/cab.py`` adjustments.

Audit trail : every PATCH writes a ``pipeline_audit_log`` row with
event ``user_shadow_ban_changed`` carrying the operator handle, the
previous / new flag values and the supplied reason.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /admin/trust-scores
# ---------------------------------------------------------------------------
@router.get(
    "/admin/trust-scores",
    dependencies=[Depends(verify_admin_key)],
)
def list_trust_scores(
    status_filter: Literal["warning", "shadow_banned", "all"] = Query(default="all", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated trust-score view for the admin queue.

    Filter values :

    - ``warning`` — ``65 <= trust_score < 75 AND total_resolved_scans >= 100``
    - ``shadow_banned`` — ``is_shadow_banned = TRUE``
    - ``all`` — every user (default) ; useful for global investigation.

    Sort order : ``trust_score ASC, total_resolved_scans DESC`` so the
    operator sees the most-suspicious heaviest-volume accounts first.
    Soft-deleted users (``is_deleted = TRUE``) are excluded — they cannot
    earn or contribute, listing them adds noise.
    """
    where: list[str] = ["is_deleted = false"]
    if status_filter == "warning":
        where.append("trust_score >= 65 AND trust_score < 75")
        where.append("total_resolved_scans >= 100")
    elif status_filter == "shadow_banned":
        where.append("is_shadow_banned = true")
    where_sql = " AND ".join(where)

    total = db.execute(
        text(f"SELECT COUNT(*) AS n FROM users WHERE {where_sql}")  # noqa: S608 — where_sql built from literals
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT id, support_id, trust_score, total_resolved_scans,
                   is_shadow_banned, trust_score_updated_at
            FROM users
            WHERE {where_sql}
            ORDER BY trust_score ASC, total_resolved_scans DESC, id ASC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608 — where_sql built from literals
        ),
        {"limit": limit, "offset": offset},
    ).fetchall()

    return {
        "total": int(total),
        "users": [
            {
                "id": str(r.id),
                "support_id": r.support_id,
                "trust_score": int(r.trust_score),
                "total_resolved_scans": int(r.total_resolved_scans),
                "is_shadow_banned": bool(r.is_shadow_banned),
                "trust_score_updated_at": (r.trust_score_updated_at.isoformat() if r.trust_score_updated_at else None),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# PATCH /admin/users/{user_id}/shadow-ban
# ---------------------------------------------------------------------------
class ShadowBanPatchRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


@router.patch(
    "/admin/users/{user_id}/shadow-ban",
    dependencies=[Depends(verify_admin_key)],
)
def patch_shadow_ban(
    user_id: uuid.UUID,
    body: ShadowBanPatchRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Toggle the ``is_shadow_banned`` flag for a user.

    Use cases :

    - ``enabled=False`` after the batch wrongly flipped a true-positive
      contributor (false-positive recovery).
    - ``enabled=True`` to hard-ban a confirmed bad actor before waiting
      for the nightly batch (fast response).

    Side effects (atomic) :

    - ``UPDATE users SET is_shadow_banned = :enabled WHERE id = :uid``
    - ``INSERT pipeline_audit_log`` with event
      ``user_shadow_ban_changed`` and payload carrying operator,
      reason, and prev/new flag values.

    Errors :

    - 404 ``user_not_found`` — no user row for ``user_id``.
    - 403 ``forbidden`` — bad ``ADMIN_API_KEY``.
    """
    row = db.execute(
        text("SELECT is_shadow_banned FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    previous = bool(row.is_shadow_banned)
    db.execute(
        text("UPDATE users SET is_shadow_banned = :v WHERE id = :uid"),
        {"v": body.enabled, "uid": str(user_id)},
    )

    payload = {
        "event": "user_shadow_ban_changed",
        "user_id": str(user_id),
        "previous": previous,
        "new": body.enabled,
        "operator": x_admin_operator,
        "reason": body.reason,
    }
    # Phase ``manual`` matches the existing ``ck_pipeline_audit_log_phase``
    # CHECK enum (extract / comprehend / match / persist / manual) — the
    # PA admin override route uses the same convention for admin-driven
    # mutations, so we follow suit. See migration 20260430_1700_paadmin.
    db.execute(
        text(
            """
            INSERT INTO pipeline_audit_log
                (phase, level, event, scan_id, payload, created_at)
            VALUES
                ('manual', 'normal', 'user_shadow_ban_changed',
                 NULL, CAST(:payload AS jsonb), clock_timestamp())
            """
        ),
        {"payload": json.dumps(payload)},
    )
    db.commit()

    return {
        "user_id": str(user_id),
        "is_shadow_banned": body.enabled,
        "previous": previous,
    }
