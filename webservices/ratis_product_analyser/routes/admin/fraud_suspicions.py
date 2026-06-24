"""Admin fraud_suspicions endpoints — anti-fraud PR5 (sprint complete).

Three endpoints :

- ``GET    /api/v1/admin/fraud_suspicions`` — paginated queue. Filters
  on ``detection_signal``, ``resolution_status`` (default ``pending``),
  ``detected_after`` / ``detected_before``, ``user_id``. Ordered
  ``detected_at DESC`` so the most recent suspicion surfaces first.
  ADMIN_API_KEY only (read-only).

- ``GET    /api/v1/admin/fraud_suspicions/{suspicion_id}`` — single
  row enriched with the triggering receipt's headline fields + every
  ``evidence_receipt`` summary (id / user_id / purchased_at /
  total_amount). The evidence list is intentionally enriched at
  fetch-time rather than denormed at insert-time : the cross-user
  receipts may shift (RGPD anonymize) before the operator reviews
  the suspicion, and we want the live state.

- ``PATCH  /api/v1/admin/fraud_suspicions/{suspicion_id}`` — resolve
  the suspicion. Body : ``{resolution_status, resolution_note}``
  with ``resolution_status`` ∈ ``{confirmed_fraud, cleared,
  escalated_support}`` (per ``RESOLUTION_STATUSES`` minus
  ``pending`` — you can't "resolve to pending"). Sets ``resolved_at
  = now()`` and stores ``admin_operator`` from the
  ``X-Admin-Operator`` header. The DB
  ``ck_fraud_suspicions_resolution_coherence`` enforces the
  invariant : non-pending requires ``resolved_at`` set.
  ADMIN_API_KEY + X-Admin-Operator.

Schema notes (ground truth = migration ``20260511_1500_afpr1`` +
``ratis_core.models.fraud_suspicions``) :

- ``evidence_receipt_ids`` is a UUID array (not a junction table),
  not a single FK — see the model module's docstring.
- ``admin_operator`` is the honor-system handle from
  ``X-Admin-Operator`` (logged at resolution-time only).
- ``resolution_note`` is singular (not ``..._notes``). The brief
  PR5 dictation said ``resolution_notes`` ; we use the canonical
  schema name to stay schema-coherent (R33 — never duplicate a
  field with a near-synonym).
- ``detected_at`` is the audit creation timestamp (not the resolve
  one — those are ``resolved_at``).

Auth pattern (mirrors ``admin/observability.py`` PR4) :

* ``ADMIN_API_KEY`` on every endpoint (Bearer header).
* ``X-Admin-Operator`` on the PATCH only — the GETs are inert reads.
* No TOTP — fraud_suspicions resolution is not financial-sensitive
  per ARCH_admin_endpoints.md § "Tier 1 (TOTP)".
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.models.fraud_suspicions import (
    DETECTION_SIGNALS,
    RESOLUTION_STATUSES,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers — operator header validation (mirrors scans.py / observability.py)
# ---------------------------------------------------------------------------
def _require_operator(x_admin_operator: str | None) -> str:
    """Return the trimmed operator handle ; raise 400 if absent / blank.

    Honor-system identifier — no crypto check, only logged into
    ``pipeline_audit_log`` + the row's ``admin_operator`` column for
    human traceability per ARCH_admin_endpoints § Auth.
    """
    if not x_admin_operator or not x_admin_operator.strip():
        raise HTTPException(status_code=400, detail="operator_required")
    return x_admin_operator.strip()


def _audit(
    db: Session,
    *,
    event: str,
    payload: dict[str, Any],
) -> None:
    """INSERT a ``pipeline_audit_log`` row at ``phase='manual'``.

    Mirrors ``admin/knowledge.py::_audit`` — best-effort, swallows
    failures so a broken audit row never masks the operator's actual
    mutation. ``scan_id`` and ``parsed_ticket_id`` are both NULL : a
    fraud_suspicion resolution is a queue-row mutation, not scoped to
    a single pipeline run, so the audit row stands alone (filterable
    by ``phase='manual'`` + ``event LIKE 'admin_fraud_suspicion_%'``).
    """
    try:
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "(phase, level, event, scan_id, parsed_ticket_id, payload) "
                "VALUES ('manual', 'normal', :event, NULL, NULL, "
                "        CAST(:payload AS jsonb))"
            ),
            {
                "event": event,
                "payload": json.dumps(payload),
            },
        )
    except Exception:
        logger.warning(
            "pipeline_audit_log insert failed (phase=manual event=%s) — best-effort skip",
            event,
            exc_info=True,
        )


def _serialize_suspicion(row: Any) -> dict[str, Any]:
    """Translate a DB row (SQLAlchemy ``Row``) into the queue response shape.

    Single source of truth so the GET-queue / GET-detail responses
    stay structurally aligned.
    """
    return {
        "id": str(row.id),
        "receipt_id": str(row.receipt_id),
        "evidence_receipt_ids": [str(r) for r in (row.evidence_receipt_ids or [])],
        "detection_signal": row.detection_signal,
        "detected_at": row.detected_at.isoformat() if row.detected_at else None,
        "resolution_status": row.resolution_status,
        "admin_operator": row.admin_operator,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolution_note": row.resolution_note,
    }


# ---------------------------------------------------------------------------
# Allowed values — sourced from the ORM frozensets, not duplicated. We
# also expose a Pydantic-friendly Literal type by listing the values
# inline (Pydantic v2 cannot consume a frozenset directly in a
# ``Literal``).
# ---------------------------------------------------------------------------
# Subset of RESOLUTION_STATUSES that an operator can SET — "pending" is
# the row's initial state, never a target.
_RESOLVE_TARGETS = frozenset(RESOLUTION_STATUSES - {"pending"})


ResolutionTarget = Literal["confirmed_fraud", "cleared", "escalated_support"]
DetectionSignalLit = Literal[
    "phash",
    "fp_global_strict",
    "fp_global_minute",
    "device_shared",
    "daily_soft_burst",
]
ResolutionStatusLit = Literal[
    "pending",
    "confirmed_fraud",
    "cleared",
    "escalated_support",
]


# Defense-in-depth — if the ORM frozensets shift but this module forgets
# to follow, fail fast at import rather than at request-time.
assert (
    frozenset(("phash", "fp_global_strict", "fp_global_minute", "device_shared", "daily_soft_burst"))
    == DETECTION_SIGNALS
), (
    "DetectionSignalLit drifted from DETECTION_SIGNALS frozenset — "
    "update routes/admin/fraud_suspicions.py to mirror the ORM."
)
assert frozenset(("confirmed_fraud", "cleared", "escalated_support")) == _RESOLVE_TARGETS, (
    "ResolutionTarget drifted from RESOLUTION_STATUSES - {'pending'} — "
    "update routes/admin/fraud_suspicions.py to mirror the ORM."
)


# ===========================================================================
# Response / request models
# ===========================================================================
class FraudSuspicionItem(BaseModel):
    """One row of the queue response (GET list + GET detail core)."""

    id: str
    receipt_id: str
    evidence_receipt_ids: list[str]
    detection_signal: str
    detected_at: str | None
    resolution_status: str
    admin_operator: str | None
    resolved_at: str | None
    resolution_note: str | None


class FraudSuspicionListResponse(BaseModel):
    items: list[FraudSuspicionItem]
    limit: int
    offset: int


class _ReceiptHeadline(BaseModel):
    id: str
    user_id: str | None
    store_id: str | None
    purchased_at: str | None
    total_amount: int | None
    image_deleted_at: str | None


class FraudSuspicionDetail(FraudSuspicionItem):
    """GET-single response — adds receipt-side context."""

    receipt: _ReceiptHeadline | None
    evidence_receipts: list[_ReceiptHeadline]


class ResolveRequest(BaseModel):
    resolution_status: ResolutionTarget
    resolution_note: str = Field(min_length=1, max_length=4000)

    model_config = {"extra": "forbid"}


# ===========================================================================
# GET /admin/fraud_suspicions  — paginated queue
# ===========================================================================
_MAX_LIMIT = 200


@router.get(
    "/admin/fraud_suspicions",
    dependencies=[Depends(verify_admin_key)],
    response_model=FraudSuspicionListResponse,
)
def list_fraud_suspicions(
    detection_signal: DetectionSignalLit | None = Query(default=None),
    resolution_status: ResolutionStatusLit | None = Query(default="pending"),
    detected_after: datetime | None = Query(default=None),
    detected_before: datetime | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> FraudSuspicionListResponse:
    """Browse the fraud_suspicions queue.

    Filters compose with AND. ``resolution_status`` defaults to
    ``pending`` because the typical operator workflow is "what's
    waiting on me ?". Pass ``resolution_status=<other>`` (or omit it
    via setting to ``null`` is not possible — Pydantic Literal — so
    use a concrete value) to see historical rows.

    Ordering is fixed (``detected_at DESC, id DESC``) — the partial
    index ``idx_fraud_suspicions_status`` only helps the pending-only
    case, but the table is small enough (V1 audit volume) that a seq
    scan on the other filters is acceptable.

    Errors :
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 422 — out-of-range ``limit`` / ``offset`` / bad enum value
    """
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if detection_signal is not None:
        clauses.append("fs.detection_signal = :sig")
        params["sig"] = detection_signal
    if resolution_status is not None:
        clauses.append("fs.resolution_status = :stat")
        params["stat"] = resolution_status
    if detected_after is not None:
        clauses.append("fs.detected_at >= :after")
        params["after"] = detected_after
    if detected_before is not None:
        clauses.append("fs.detected_at <= :before")
        params["before"] = detected_before
    if user_id is not None:
        clauses.append("r.user_id = :uid")
        params["uid"] = str(user_id)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        "SELECT fs.id, fs.receipt_id, fs.evidence_receipt_ids, "
        "       fs.detection_signal, fs.detected_at, fs.resolution_status, "
        "       fs.admin_operator, fs.resolved_at, fs.resolution_note "
        "FROM fraud_suspicions fs "
        "LEFT JOIN receipts r ON r.id = fs.receipt_id " + where_sql + " ORDER BY fs.detected_at DESC, fs.id DESC "
        "LIMIT :limit OFFSET :offset"
    )
    rows = db.execute(text(sql), params).fetchall()
    items = [FraudSuspicionItem(**_serialize_suspicion(r)) for r in rows]
    return FraudSuspicionListResponse(items=items, limit=limit, offset=offset)


# ===========================================================================
# GET /admin/fraud_suspicions/{suspicion_id}  — single + receipt context
# ===========================================================================
@router.get(
    "/admin/fraud_suspicions/{suspicion_id}",
    dependencies=[Depends(verify_admin_key)],
    response_model=FraudSuspicionDetail,
)
def get_fraud_suspicion(
    suspicion_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> FraudSuspicionDetail:
    """Return one fraud_suspicion enriched with the triggering receipt
    and the evidence receipts (live state, not snapshot).

    Errors :
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``fraud_suspicion_not_found``
    """
    row = db.execute(
        text(
            "SELECT id, receipt_id, evidence_receipt_ids, detection_signal, "
            "       detected_at, resolution_status, admin_operator, "
            "       resolved_at, resolution_note "
            "FROM fraud_suspicions WHERE id = :sid"
        ),
        {"sid": str(suspicion_id)},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="fraud_suspicion_not_found")

    receipt_ids = {row.receipt_id, *(row.evidence_receipt_ids or [])}
    receipt_rows = db.execute(
        text(
            "SELECT id, user_id, store_id, purchased_at, total_amount, "
            "       image_deleted_at "
            "FROM receipts WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": [str(r) for r in receipt_ids]},
    ).fetchall()
    by_id: dict[str, _ReceiptHeadline] = {
        str(r.id): _ReceiptHeadline(
            id=str(r.id),
            user_id=str(r.user_id) if r.user_id else None,
            store_id=str(r.store_id) if r.store_id else None,
            purchased_at=r.purchased_at.isoformat() if r.purchased_at else None,
            total_amount=r.total_amount,
            image_deleted_at=(r.image_deleted_at.isoformat() if r.image_deleted_at else None),
        )
        for r in receipt_rows
    }

    core = _serialize_suspicion(row)
    return FraudSuspicionDetail(
        **core,
        receipt=by_id.get(str(row.receipt_id)),
        evidence_receipts=[by_id[eid] for eid in core["evidence_receipt_ids"] if eid in by_id],
    )


# ===========================================================================
# PATCH /admin/fraud_suspicions/{suspicion_id}  — resolve / dismiss / escalate
# ===========================================================================
@router.patch(
    "/admin/fraud_suspicions/{suspicion_id}",
    dependencies=[Depends(verify_admin_key)],
    response_model=FraudSuspicionItem,
)
def resolve_fraud_suspicion(
    suspicion_id: uuid.UUID,
    body: ResolveRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> FraudSuspicionItem:
    """Mark a fraud_suspicion as ``confirmed_fraud`` / ``cleared`` /
    ``escalated_support``. Single-shot — once resolved, a second PATCH
    on the same row returns 409 ``already_resolved`` (the row is
    immutable in V1, cf model docstring).

    Body :
        ``resolution_status`` — one of the three resolve targets
        (``pending`` rejected at the Pydantic layer).
        ``resolution_note`` — free-form text (1-4000 chars,
        required). Audit-grade narrative for the resolution.

    Errors :
        - 400 ``operator_required`` — missing X-Admin-Operator header
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``fraud_suspicion_not_found``
        - 409 ``already_resolved`` — row already left ``pending``
        - 422 — Pydantic validation (bad ``resolution_status``,
          missing ``resolution_note``, extra fields)
    """
    operator = _require_operator(x_admin_operator)

    # Lock the row — race two concurrent PATCHes on the same id : the
    # second one should see ``resolution_status != 'pending'`` after the
    # first commits, and surface 409 instead of overwriting.
    locked = db.execute(
        text("SELECT id, resolution_status FROM fraud_suspicions WHERE id = :sid FOR UPDATE"),
        {"sid": str(suspicion_id)},
    ).first()
    if locked is None:
        raise HTTPException(status_code=404, detail="fraud_suspicion_not_found")
    if locked.resolution_status != "pending":
        raise HTTPException(status_code=409, detail="already_resolved")

    updated = db.execute(
        text(
            "UPDATE fraud_suspicions "
            "SET resolution_status = :stat, "
            "    resolution_note = :note, "
            "    admin_operator = :op, "
            "    resolved_at = now() "
            "WHERE id = :sid "
            "RETURNING id, receipt_id, evidence_receipt_ids, detection_signal, "
            "          detected_at, resolution_status, admin_operator, "
            "          resolved_at, resolution_note"
        ),
        {
            "sid": str(suspicion_id),
            "stat": body.resolution_status,
            "note": body.resolution_note,
            "op": operator,
        },
    ).first()
    # Defense-in-depth — the FOR UPDATE above guarantees the row exists,
    # but if the DB CHECK ``ck_fraud_suspicions_resolution_coherence``
    # somehow rejects the UPDATE, ``RETURNING`` is empty and we surface
    # an explicit 500 rather than a silent succeed-with-no-row.
    if updated is None:  # pragma: no cover — defensive only
        logger.error(
            "fraud_suspicion %s UPDATE returned no row despite FOR UPDATE — likely a CHECK constraint regression",
            suspicion_id,
        )
        raise HTTPException(status_code=500, detail="internal_server_error")

    _audit(
        db,
        event=f"admin_fraud_suspicion_{body.resolution_status}",
        payload={
            "operator": operator,
            "suspicion_id": str(suspicion_id),
            "receipt_id": str(updated.receipt_id),
            "detection_signal": updated.detection_signal,
            "resolution_status": body.resolution_status,
            "resolution_note": body.resolution_note,
        },
    )

    db.commit()
    return FraudSuspicionItem(**_serialize_suspicion(updated))
