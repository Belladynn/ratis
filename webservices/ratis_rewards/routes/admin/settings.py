"""Admin REST endpoints for ``app_settings`` runtime configuration.

Existing (gardés depuis Bloc A) :
    GET  /admin/settings                        — toutes les sections
    GET  /admin/settings/{section}              — une section
    POST /admin/settings/seed                   — re-seed depuis ratis_settings.json

Renforcés / ajoutés (Bloc B) :
    PUT    /admin/settings/{section}                  — write avec audit + 2FA grace + allowlist
    GET    /admin/settings/audit                      — listing audit log paginé
    GET    /admin/settings/audit/{audit_id}           — détail audit row + diff on-fly
    POST   /admin/settings/{section}/confirm-2fa      — confirme un pending_2fa via TOTP
    POST   /admin/settings/{section}/cancel-pending   — cancel un pending_2fa
    GET    /admin/settings/{section}/editable         — bool + frozen_keys allowlist

Auth : ``ADMIN_API_KEY`` partout (``verify_admin_key``). ``X-Admin-TOTP``
ajoutée uniquement sur ``confirm-2fa``. L'``X-Admin-Operator`` header est
propagé en audit log (PUT, confirm-2fa, cancel-pending).

ARCH : ``ARCH_admin_settings.md`` § Endpoints + § Garde-fous V1.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from services.admin.settings_service import (
    get_frozen_keys,
    is_editable,
    redact_for_audit,
    update_settings_section_with_2fa_check,
    validate_body_size,
)
from services.totp_service import verify_totp_dep
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _operator(request: Request) -> str:
    """Extract X-Admin-Operator header (defaults to ``unknown`` for legacy callers).

    The header is mandatory in the audit row but we default to ``unknown``
    rather than 400-ing — clients that pre-date Bloc B (curl scripts, ops
    runbooks) keep working ; the audit row makes the legacy origin obvious.
    """
    return request.headers.get("X-Admin-Operator", "unknown")


def _shallow_diff(old_data: dict[str, Any] | None, new_data: dict[str, Any]) -> dict[str, list[str]]:
    """Lazy fallback for rows where ``diff`` is NULL (forward-compat).

    Mirrors the shape produced by ``ratis_core.settings._compute_diff``
    (``{added, removed, changed}``) without crossing the underscore
    boundary into ratis_core internals — keeps the dependency direction
    clean even if Bloc A renames its private helper.
    """
    if old_data is None:
        return {"added": sorted(new_data.keys()), "removed": [], "changed": []}
    added = sorted(k for k in new_data if k not in old_data)
    removed = sorted(k for k in old_data if k not in new_data)
    changed = sorted(k for k in new_data if k in old_data and new_data[k] != old_data[k])
    return {"added": added, "removed": removed, "changed": changed}


def _serialize_audit(row: Any, *, include_diff: bool = True) -> dict[str, Any]:
    """Map an ``admin_settings_audit`` row to a JSON-safe dict.

    ``diff`` is computed on-fly when the column is NULL (older rows from
    a hypothetical pre-Bloc A history would not have a pre-computed diff,
    but Bloc A always writes one — kept defensive for forward-compat).

    M3 — sensitive sub-keys (e.g. ``subscription_promotions.active_codes``)
    are masked via :func:`redact_for_audit` before exposure. The DB row
    is left untouched (legal audit trail).
    """
    out: dict[str, Any] = {
        "id": str(row.id),
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "operator": row.operator,
        "section": row.section,
        "reason": row.reason,
        "status": row.status if isinstance(row.status, str) else row.status.value,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
    }
    if include_diff:
        old_data = redact_for_audit(row.section, row.old_data)
        new_data = redact_for_audit(row.section, row.new_data)
        diff = row.diff
        if diff is None:
            diff = _shallow_diff(old_data, new_data)
        out["diff"] = diff
        out["old_data"] = old_data
        out["new_data"] = new_data
    return out


# ---------------------------------------------------------------------------
# GET /admin/settings — list all sections
# ---------------------------------------------------------------------------
@router.get(
    "/admin/settings",
    dependencies=[Depends(verify_admin_key)],
)
def list_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return all settings sections stored in DB."""
    rows = db.execute(text("SELECT section, data FROM app_settings ORDER BY section")).fetchall()
    return {row.section: row.data for row in rows}


# ---------------------------------------------------------------------------
# GET /admin/settings/audit — paginated audit log
# (declared BEFORE /admin/settings/{section} so FastAPI does not match
# ``audit`` as a section name — path-param routes are evaluated in order.)
# ---------------------------------------------------------------------------
@router.get(
    "/admin/settings/audit",
    dependencies=[Depends(verify_admin_key)],
)
def list_audit(
    section: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List audit log entries.

    Filters :
        - ``section`` (optional) — exact match.
        - ``status`` (optional) — applied | pending_2fa | expired | cancelled.

    Pagination : ``limit`` (1..100, default 20), ``offset`` (default 0).
    Cursor-based pagination is V2 — limit/offset is sufficient at alpha
    scale (audit log < 1k rows total).

    Returns ``{items: [...], total: int, limit, offset}``. Items omit the
    ``old_data`` / ``new_data`` payloads to keep the response small ;
    use :func:`get_audit_detail` for the full row.
    """
    from ratis_core.models.admin_audit import AdminSettingsAudit

    q = db.query(AdminSettingsAudit)
    if section is not None:
        q = q.filter(AdminSettingsAudit.section == section)
    if status_filter is not None:
        q = q.filter(AdminSettingsAudit.status == status_filter)

    total = q.count()
    rows = q.order_by(AdminSettingsAudit.timestamp.desc()).limit(limit).offset(offset).all()
    items = [_serialize_audit(r, include_diff=False) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# GET /admin/settings/audit/{audit_id} — detail with diff
# ---------------------------------------------------------------------------
@router.get(
    "/admin/settings/audit/{audit_id}",
    dependencies=[Depends(verify_admin_key)],
)
def get_audit_detail(
    audit_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a full audit row including diff (computed on-fly if NULL)."""
    from ratis_core.models.admin_audit import AdminSettingsAudit

    row = db.query(AdminSettingsAudit).filter(AdminSettingsAudit.id == audit_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return _serialize_audit(row, include_diff=True)


# ---------------------------------------------------------------------------
# POST /admin/settings/seed — re-seed from JSON (existing)
# ---------------------------------------------------------------------------
@router.post(
    "/admin/settings/seed",
    dependencies=[Depends(verify_admin_key)],
)
def seed_from_json(db: Session = Depends(get_db)) -> dict[str, int]:
    """Re-seed all sections from ratis_settings.json. Safe to call multiple times."""
    from ratis_core.seed_settings import seed_settings

    n = seed_settings(db)
    return {"seeded": n}


# ---------------------------------------------------------------------------
# GET /admin/settings/{section}/editable — allowlist introspection
# ---------------------------------------------------------------------------
@router.get(
    "/admin/settings/{section}/editable",
    dependencies=[Depends(verify_admin_key)],
)
def get_editable(section: str) -> dict[str, Any]:
    """Return ``{editable: bool, frozen_keys: [...]}`` for a section.

    Used by the UI to decide between read-only preview and editable form.
    Sections absent from :data:`EDITABLE_SECTIONS` are reported as
    non-editable with an empty ``frozen_keys`` list.
    """
    return {
        "editable": is_editable(section),
        "frozen_keys": sorted(get_frozen_keys(section)),
    }


# ---------------------------------------------------------------------------
# GET /admin/settings/{section} — single section
# ---------------------------------------------------------------------------
@router.get(
    "/admin/settings/{section}",
    dependencies=[Depends(verify_admin_key)],
)
def get_section(section: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return a single settings section."""
    row = db.execute(
        text("SELECT data FROM app_settings WHERE section = :section"),
        {"section": section},
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="settings_section_not_found")
    return row.data


# ---------------------------------------------------------------------------
# PUT /admin/settings/{section} — write with audit + allowlist + 2FA grace
# ---------------------------------------------------------------------------
class PutSectionBody(BaseModel):
    """Body schema for PUT /admin/settings/{section}.

    ``data`` is the candidate replacement payload (full replace — no merge).
    The body-size cap (M5, 64 KB) is enforced inside the route via
    :func:`validate_body_size` after parse — Pydantic does not natively
    bound a ``dict`` field's serialized footprint.

    ``reason`` is the business motivation, mandatory at API level :
    ≥ 8 chars (re-checked by the DB CHECK constraint as defense in depth)
    and ≤ 2000 chars (L2, audit sécurité 2026-05-03 — bounded free-text).
    """

    data: dict[str, Any]
    reason: str = Field(..., min_length=8, max_length=2000)


@router.put(
    "/admin/settings/{section}",
    dependencies=[Depends(verify_admin_key)],
)
def put_section(
    section: str,
    body: PutSectionBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Replace section data (full replace).

    Returns ``{audit_id, status}`` where ``status`` is ``"applied"`` (value
    persisted in app_settings) or ``"pending_2fa"`` (variation > 50 %, held
    for TOTP confirmation within the grace period).

    Errors :
        - 403 ``section_frozen`` — section not in EDITABLE_SECTIONS.
        - 403 ``frozen_key_modified`` — a frozen sub-key was changed.
          Body : ``{"detail": "frozen_key_modified", "key": "<name>"}``.
        - 413 ``payload_too_large`` — ``data`` JSON serialized > 64 KB
          (M5 audit sécurité 2026-05-03). Defense against DoS via huge
          push from a malicious operator with ADMIN_API_KEY.
        - 422 ``reason_too_short`` — ``len(reason) < 8`` (also enforced
          by Pydantic ``min_length`` but kept as a fallback).
    """
    # M5 — cap before any DB work so a multi-MB payload cannot DoS us.
    # The Pydantic model is already parsed at this point ; the cap on
    # the serialized body bounds memory + audit-row size at insert time.
    validate_body_size(body.data)
    operator = _operator(request)
    with db_transaction(db):
        audit_id, status_str = update_settings_section_with_2fa_check(
            db,
            section,
            body.data,
            operator=operator,
            reason=body.reason,
        )
    return {"audit_id": str(audit_id), "status": status_str}


# ---------------------------------------------------------------------------
# POST /admin/settings/{section}/confirm-2fa — TOTP confirmation
# ---------------------------------------------------------------------------
class ConfirmCancelBody(BaseModel):
    audit_id: uuid.UUID


@router.post(
    "/admin/settings/{section}/confirm-2fa",
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def confirm_2fa(
    section: str,
    body: ConfirmCancelBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Confirm a ``pending_2fa`` audit row via TOTP.

    1. Lookup the audit row by id ; must be ``status='pending_2fa'`` and
       ``expires_at > now()``.
    2. Apply ``new_data`` to ``app_settings`` (UPSERT) and transition the
       audit row to ``applied`` (``applied_at = now()``). No new audit row
       is created — the original pending row is the historical record.
    3. Returns ``{audit_id, status: 'applied'}``.

    Errors :
        - 401 ``totp_required`` / ``totp_invalid`` — via ``verify_totp_dep``.
        - 404 ``audit_not_found`` — id absent or section mismatch.
        - 409 ``audit_not_pending`` — status already applied/expired/cancelled.
        - 410 ``audit_expired`` — ``expires_at < now()`` (also flips status
          to ``expired`` lazily).
    """
    from ratis_core.models.admin_audit import (
        AdminSettingsAudit,
        AdminSettingsAuditStatus,
    )

    # H1 — cross-operator attribution guard. The operator confirming the
    # 2FA MUST be the same one who initiated the pending change. The audit
    # row records the original operator ; we filter by it so a pending row
    # is only resolvable by its author. A different operator gets a 404
    # rather than a 403 — we deliberately do not reveal that the row
    # exists under another operator (no information leak between admins).
    operator = _operator(request)

    with db_transaction(db):
        row = (
            db.query(AdminSettingsAudit)
            .filter(AdminSettingsAudit.id == body.audit_id)
            .filter(AdminSettingsAudit.section == section)
            .filter(AdminSettingsAudit.operator == operator)
            .with_for_update()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="audit_not_found")
        # Compare status as raw string — SQLEnum returns the enum member
        # but the str mixin on AdminSettingsAuditStatus makes both flavours
        # equivalent for ``==`` checks against the .value.
        current_status = row.status if isinstance(row.status, str) else row.status.value
        if current_status != AdminSettingsAuditStatus.PENDING_2FA.value:
            raise HTTPException(status_code=409, detail="audit_not_pending")

        # ``expires_at`` is TIMESTAMPTZ both in prod (migration) and tests
        # (model declares ``TIMESTAMP(timezone=True)`` since KP-44 fix), so
        # both sides of the comparison are aware UTC.
        now = datetime.now(UTC)
        if row.expires_at is None or row.expires_at < now:
            row.status = AdminSettingsAuditStatus.EXPIRED.value
            db.flush()
            raise HTTPException(status_code=410, detail="audit_expired")

        # Apply the queued payload via UPSERT (mirrors the SQL used by
        # update_settings_section's bypass_2fa path).
        db.execute(
            text(
                "INSERT INTO app_settings (section, data, updated_at)"
                " VALUES (:section, CAST(:data AS JSONB), now())"
                " ON CONFLICT (section) DO UPDATE"
                " SET data = EXCLUDED.data, updated_at = now()"
            ),
            {"section": section, "data": json.dumps(row.new_data)},
        )
        row.status = AdminSettingsAuditStatus.APPLIED.value
        row.applied_at = now  # aware UTC (model is TIMESTAMPTZ)
        db.flush()

    return {"audit_id": str(body.audit_id), "status": "applied"}


# ---------------------------------------------------------------------------
# POST /admin/settings/{section}/cancel-pending — cancel pending_2fa
# ---------------------------------------------------------------------------
@router.post(
    "/admin/settings/{section}/cancel-pending",
    dependencies=[Depends(verify_admin_key)],
)
def cancel_pending(
    section: str,
    body: ConfirmCancelBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cancel a ``pending_2fa`` audit row — transitions to ``cancelled``.

    No TOTP required : cancelling has no side effect on ``app_settings``,
    only on the audit row state. The same 404 / 409 / 410 errors as
    :func:`confirm_2fa` apply (modulo the absence of TOTP failures).
    """
    from ratis_core.models.admin_audit import (
        AdminSettingsAudit,
        AdminSettingsAuditStatus,
    )

    _ = _operator(request)

    with db_transaction(db):
        row = (
            db.query(AdminSettingsAudit)
            .filter(AdminSettingsAudit.id == body.audit_id)
            .filter(AdminSettingsAudit.section == section)
            .with_for_update()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="audit_not_found")
        current_status = row.status if isinstance(row.status, str) else row.status.value
        if current_status != AdminSettingsAuditStatus.PENDING_2FA.value:
            raise HTTPException(status_code=409, detail="audit_not_pending")

        # Honour the grace period the same way confirm_2fa does — caller
        # cancelling after expiry sees a 410 ``audit_expired`` rather than
        # a misleading 200. Both sides aware UTC since KP-44 fix.
        now = datetime.now(UTC)
        if row.expires_at is None or row.expires_at < now:
            row.status = AdminSettingsAuditStatus.EXPIRED.value
            db.flush()
            raise HTTPException(status_code=410, detail="audit_expired")

        row.status = AdminSettingsAuditStatus.CANCELLED.value
        db.flush()

    return {"audit_id": str(body.audit_id), "status": "cancelled"}
