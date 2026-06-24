"""Admin JSON API for the Name Resolution Consensus arbitration queue
(NRC bloc D — see ``ARCH_name_resolution_consensus.md`` § "Plan
d'implémentation par blocs", row D).

Five endpoints, all gated by ``verify_admin_key`` + a non-empty
``X-Admin-Operator`` header (mirrors :mod:`routes.admin.scans`) :

- ``GET    /admin/name-resolutions/queue``
- ``GET    /admin/name-resolutions/unmatched``
- ``GET    /admin/name-resolutions/{store_id}/{normalized_label}``
- ``POST   /admin/name-resolutions/resolve``
- ``POST   /admin/name-resolutions/{store_id}/{normalized_label}/escalate``
- ``POST   /admin/name-resolutions/reject-challenges``

The detail endpoint accepts the ``normalized_label`` as a path param —
labels are uppercase and free of slashes after the
:func:`worker.ocr.matcher._normalize_text` pipeline, but we still
URL-encode them in the UI ; FastAPI decodes path params per-segment.

Error contract (``{"detail": "snake_code"}`` per R12) :

- 400 ``operator_required``        — missing X-Admin-Operator
- 400 ``invalid_state``            — query param ``state`` not in enum
- 400 ``operator_note_too_long``   — > 300 chars
- 403 ``forbidden``                — wrong / missing ADMIN_API_KEY
- 404 ``label_not_found``          — pair has no ledger row + no scans
- 422 ``state_mismatch``           — reject-challenges on non-unverified
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from services import name_resolution_admin_service as nrc_admin
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# Mirrors the constraint on the underlying queue helper. Kept here so
# the OpenAPI surface lists the accepted values inline.
_ALLOWED_QUEUE_STATES = ("unverified", "controverse", "all")

# Operator-note hard cap — prevents abuse / accidental log explosion.
# Cross-checked client-side by the mini UI textarea maxlength.
_MAX_NOTE_LENGTH = 300


# ===========================================================================
# Helpers
# ===========================================================================


def _require_operator(x_admin_operator: str | None) -> str:
    """Enforce the X-Admin-Operator honor-system identifier.

    Same shape as :func:`routes.admin.scans._require_operator` — kept
    private rather than importing across route modules to avoid
    circular-import tangles when one module is unmounted (e.g. when
    ADMIN_API_KEY is missing).
    """
    if not x_admin_operator or not x_admin_operator.strip():
        raise HTTPException(status_code=400, detail="operator_required")
    return x_admin_operator.strip()


def _check_note(note: str | None) -> str | None:
    """Length guard for free-form operator notes."""
    if note is None:
        return None
    cleaned = note.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_NOTE_LENGTH:
        raise HTTPException(status_code=400, detail="operator_note_too_long")
    return cleaned


def _serialize_queue_item(item: nrc_admin.QueueItem) -> dict[str, Any]:
    """Translate the dataclass to a JSON-friendly dict."""
    return {
        "store_id": item.store_id,
        "store_name": item.store_name,
        "normalized_label": item.normalized_label,
        "current_state": item.current_state,
        "distinct_validators": item.distinct_validators,
        "top_eans": [
            {
                "ean": t.ean,
                "weighted_count": t.weighted_count,
                "pct": t.pct,
                "product_name": t.product_name,
            }
            for t in item.top_eans
        ],
        "previously_verified_ean": item.previously_verified_ean,
        "first_resolution_at": item.first_resolution_at,
        "last_resolution_at": item.last_resolution_at,
        "challenger_count": item.challenger_count,
        "sample_scans": [
            {
                "scan_id": s.scan_id,
                "scanned_name": s.scanned_name,
                "user_id": s.user_id,
            }
            for s in item.sample_scans
        ],
    }


def _serialize_unmatched_item(item: nrc_admin.UnmatchedItem) -> dict[str, Any]:
    return {
        "store_id": item.store_id,
        "store_name": item.store_name,
        "normalized_label": item.normalized_label,
        "scan_count": item.scan_count,
        "sample_scans": [
            {
                "scan_id": s.scan_id,
                "scanned_name": s.scanned_name,
                "user_id": s.user_id,
            }
            for s in item.sample_scans
        ],
        "top_candidates": item.top_candidates,
    }


# ===========================================================================
# GET /admin/name-resolutions/queue
# ===========================================================================
@router.get(
    "/admin/name-resolutions/queue",
    dependencies=[Depends(verify_admin_key)],
)
def list_queue(
    state: str = Query(default="all"),
    store_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated arbitration queue (UNVERIFIED first, then CONTROVERSE).

    Read-only — no operator header required (mirrors
    :func:`routes.admin.observability.get_audit_log`). Mutations have
    their own header gate downstream.
    """
    if state not in _ALLOWED_QUEUE_STATES:
        raise HTTPException(status_code=400, detail="invalid_state")
    items, total = nrc_admin.list_arbitration_queue(
        db,
        state_filter=state,
        store_id=store_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_serialize_queue_item(i) for i in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ===========================================================================
# GET /admin/name-resolutions/unmatched
# ===========================================================================
@router.get(
    "/admin/name-resolutions/unmatched",
    dependencies=[Depends(verify_admin_key)],
)
def list_unmatched(
    store_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated scans without consensus and with stored fuzzy candidates.

    Grouped by ``(store_id, normalized_label)``. Each group exposes the
    aggregated top fuzzy candidates so the operator can pick one and
    POST it through ``/resolve`` without manually inspecting JSONB.
    """
    items, total = nrc_admin.list_unmatched_queue(db, store_id=store_id, limit=limit, offset=offset)
    return {
        "items": [_serialize_unmatched_item(i) for i in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ===========================================================================
# GET /admin/name-resolutions/{store_id}/{normalized_label}
# ===========================================================================
@router.get(
    "/admin/name-resolutions/{store_id}/{normalized_label:path}",
    dependencies=[Depends(verify_admin_key)],
)
def get_detail(
    store_id: uuid.UUID,
    normalized_label: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate every ledger row + state-change event for a label.

    The ``normalized_label`` segment uses the ``:path`` converter so a
    label containing spaces / slashes / unicode is preserved intact.
    """
    try:
        return nrc_admin.get_label_detail(db, store_id=store_id, normalized_label=normalized_label)
    except nrc_admin.LabelNotFound:
        raise HTTPException(status_code=404, detail="label_not_found")


# ===========================================================================
# POST /admin/name-resolutions/resolve
# ===========================================================================
class ResolveRequest(BaseModel):
    store_id: uuid.UUID
    normalized_label: str = Field(min_length=1)
    target_ean: str = Field(min_length=1)
    operator_note: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


@router.post(
    "/admin/name-resolutions/resolve",
    dependencies=[Depends(verify_admin_key)],
)
def resolve(
    body: ResolveRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Tranche un cas controverse/unverified/unmatched en faveur de ``target_ean``."""
    operator = _require_operator(x_admin_operator)
    note = _check_note(body.operator_note)

    try:
        outcome = nrc_admin.resolve_label(
            db,
            store_id=body.store_id,
            normalized_label=body.normalized_label,
            target_ean=body.target_ean,
            operator=operator,
            operator_note=note,
        )
    except nrc_admin.LabelNotFound:
        raise HTTPException(status_code=404, detail="label_not_found")

    db.commit()
    return outcome


# ===========================================================================
# POST /admin/name-resolutions/reject-challenges
# ===========================================================================
class RejectChallengesRequest(BaseModel):
    store_id: uuid.UUID
    normalized_label: str = Field(min_length=1)
    operator_note: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


@router.post(
    "/admin/name-resolutions/reject-challenges",
    dependencies=[Depends(verify_admin_key)],
)
def reject_challenges(
    body: RejectChallengesRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Re-promote the previously-verified EAN by appending ``manual_admin``.

    Only valid when the current state is ``unverified``.
    """
    operator = _require_operator(x_admin_operator)
    note = _check_note(body.operator_note)

    try:
        outcome = nrc_admin.reject_challenges(
            db,
            store_id=body.store_id,
            normalized_label=body.normalized_label,
            operator=operator,
            operator_note=note,
        )
    except nrc_admin.LabelNotFound:
        raise HTTPException(status_code=404, detail="label_not_found")
    except nrc_admin.StateMismatch:
        raise HTTPException(status_code=422, detail="state_mismatch")

    db.commit()
    return outcome


# ===========================================================================
# POST /admin/name-resolutions/{store_id}/{normalized_label}/escalate
# ===========================================================================
class EscalateRequest(BaseModel):
    operator_note: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


@router.post(
    "/admin/name-resolutions/{store_id}/{normalized_label:path}/escalate",
    dependencies=[Depends(verify_admin_key)],
)
def escalate(
    store_id: uuid.UUID,
    normalized_label: str = Path(..., min_length=1),
    body: EscalateRequest = EscalateRequest(),
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Flag a label for priorisation manuel (audit-only, no side-effect)."""
    operator = _require_operator(x_admin_operator)
    note = _check_note(body.operator_note)

    try:
        outcome = nrc_admin.escalate_label(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            operator=operator,
            operator_note=note,
        )
    except nrc_admin.LabelNotFound:
        raise HTTPException(status_code=404, detail="label_not_found")

    db.commit()
    return outcome
