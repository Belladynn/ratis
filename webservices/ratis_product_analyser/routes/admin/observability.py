"""Admin observability endpoints — ARCH_admin_endpoints.md PR4.

Five endpoints :

- ``GET  /api/v1/admin/pipeline/audit-log`` — paginated lineage debug.
  Filters on ``receipt_id`` / ``parsed_ticket_id`` / ``scan_id`` /
  ``since`` / ``phase`` / ``level``. Ordered by ``created_at`` DESC so
  the operator sees the latest events first. ADMIN_API_KEY only.

- ``GET  /api/v1/admin/parsed-tickets/{parsed_ticket_id}`` — full state
  of one parsed ticket : the row itself + every scan that links back to
  it + every audit event scoped to it (via ``parsed_ticket_id`` or any
  scan's ``scan_id``). ADMIN_API_KEY only.

- ``GET  /api/v1/admin/parsed-tickets`` — paginated browse, filterable
  by derived ``status`` (``matched`` / ``unresolved`` / ``rejected`` /
  ``mixed``). The status is **derived from the linked scans**, not
  stored on ``parsed_tickets`` (that table is intentionally immutable
  per ARCH § Cardinal state). The derivation rule :

    * ``matched``    — every linked scan has ``status='matched'``
    * ``rejected``   — every linked scan has ``status='rejected'``
    * ``unresolved`` — every linked scan has ``status='unresolved'``
    * ``mixed``      — any other combination (including 0 scans)

  ADMIN_API_KEY only.

- ``POST /api/v1/admin/parsed-tickets/{parsed_ticket_id}/replay`` —
  async dispatch of a Celery task that re-runs Phase 3 + Phase 4 on the
  persisted ParsedTicket. The task re-instantiates the Pydantic
  ``ParsedTicket`` from the stored ``parsed_jsonb`` (so we never re-run
  OCR/LLM — cf. ARCH § Reproductibilité), wires fresh DB lookups, and
  funnels the result through ``persist_pipeline_result``. The persist
  layer's idempotence (UNIQUE ``parsed_jsonb_hash`` + the
  ``handle_barcode_rescan`` helper) guarantees no duplicate scans on
  re-runs. ADMIN_API_KEY + X-Admin-Operator header on the mutation.

- ``GET  /api/v1/admin/tasks/{task_id}/status`` — Celery task polling.
  Returns one of ``{pending, started, success, failure}`` plus the
  task's return value (``result`` on success, ``error`` on failure).
  ADMIN_API_KEY only.

Auth pattern (mirrors PA admin/scans.py PR3) :

* ``ADMIN_API_KEY`` on every endpoint (Bearer header).
* ``X-Admin-Operator`` (logged in ``pipeline_audit_log``) on the mutation
  only — the GET endpoints are inert reads.

Replay = ASYNC (vs the SYNC scan-level replay-match in PR3) because a
parsed ticket can carry tens of items, each running a fuzzy lookup ;
keeping it on the request thread would block under load.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers — operator header validation (mirrors scans.py / barcode.py)
# ---------------------------------------------------------------------------
def _require_operator(x_admin_operator: str | None) -> str:
    """Return the trimmed operator handle ; raise 400 if absent / blank."""
    if not x_admin_operator or not x_admin_operator.strip():
        raise HTTPException(status_code=400, detail="operator_required")
    return x_admin_operator.strip()


# Module-level dispatch indirection : tests monkeypatch this attribute to
# replace ``.delay`` with a stub that returns a fake AsyncResult, the same
# pattern already used by ``routes.admin.barcode._dispatch_reparse_task``.
def _dispatch_replay_task(
    *,
    parsed_ticket_id: uuid.UUID,
    admin_operator: str,
    log_level: str,
):
    """Dispatch the parsed-ticket replay Celery task asynchronously.

    Late import — keeps the route module light and avoids dragging the
    worker namespace (and its OCR-heavy transitive imports) into FastAPI
    startup.
    """
    from worker.pipeline_replay_task import replay_parsed_ticket

    return replay_parsed_ticket.delay(
        parsed_ticket_id=str(parsed_ticket_id),
        admin_operator=admin_operator,
        log_level=log_level,
    )


# ===========================================================================
# GET /admin/pipeline/audit-log  — paginated lineage
# ===========================================================================
class AuditLogRow(BaseModel):
    """One row of the paginated audit log response."""

    id: uuid.UUID
    parsed_ticket_id: uuid.UUID | None
    scan_id: uuid.UUID | None
    phase: str
    level: str
    event: str
    payload: dict[str, Any]
    created_at: datetime


_ALLOWED_PHASES = {"extract", "comprehend", "match", "persist", "manual"}
_ALLOWED_LEVELS = {"verbose", "normal", "production"}


@router.get(
    "/admin/pipeline/audit-log",
    dependencies=[Depends(verify_admin_key)],
    response_model=list[AuditLogRow],
)
def list_audit_log(
    receipt_id: uuid.UUID | None = Query(default=None),
    parsed_ticket_id: uuid.UUID | None = Query(default=None),
    scan_id: uuid.UUID | None = Query(default=None),
    since: datetime | None = Query(default=None),
    phase: str | None = Query(default=None),
    level: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[AuditLogRow]:
    """Filter ``pipeline_audit_log`` rows for lineage debug.

    All filter args are optional — combining them ANDs them together.
    ``receipt_id`` is resolved via the receipt's ``parsed_ticket_id``
    (audit rows are scoped per parsed_ticket, not per receipt). When the
    receipt has no parsed_ticket the endpoint returns an empty list
    rather than 404 — the caller polls during processing and a
    not-yet-persisted receipt is a legitimate "no events yet" case.

    Errors :
        - 400 ``invalid_phase`` / ``invalid_level`` — value not in enum
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
    """
    if phase is not None and phase not in _ALLOWED_PHASES:
        raise HTTPException(status_code=400, detail="invalid_phase")
    if level is not None and level not in _ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail="invalid_level")

    # Resolve receipt_id → parsed_ticket_id. We do not also include scans
    # by receipt_id : the audit log is keyed by parsed_ticket / scan, and
    # adding a scan-level join here would require denormalizing the
    # receipt FK on every audit row. The 360 view already covers the
    # combined surface for one receipt.
    resolved_pt_id: uuid.UUID | None = parsed_ticket_id
    if receipt_id is not None:
        rcpt = db.execute(
            text("SELECT parsed_ticket_id FROM receipts WHERE id = :rid"),
            {"rid": str(receipt_id)},
        ).first()
        if rcpt is None or rcpt.parsed_ticket_id is None:
            return []
        # If the caller passed BOTH receipt_id and parsed_ticket_id, honor
        # the explicit pt_id (more specific) and ignore the receipt's.
        if resolved_pt_id is None:
            resolved_pt_id = rcpt.parsed_ticket_id

    # Build the WHERE clause progressively — every filter is ANDed.
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if resolved_pt_id is not None:
        clauses.append("parsed_ticket_id = :pt_id")
        params["pt_id"] = str(resolved_pt_id)
    if scan_id is not None:
        clauses.append("scan_id = :scan_id")
        params["scan_id"] = str(scan_id)
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    if phase is not None:
        clauses.append("phase = :phase")
        params["phase"] = phase
    if level is not None:
        clauses.append("level = :level")
        params["level"] = level

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT id, parsed_ticket_id, scan_id, phase, level, event, "
        "       payload, created_at "
        "FROM pipeline_audit_log " + where_sql + " ORDER BY created_at DESC, id DESC "
        "LIMIT :limit OFFSET :offset"
    )
    rows = db.execute(text(sql), params).fetchall()
    return [
        AuditLogRow(
            id=r.id,
            parsed_ticket_id=r.parsed_ticket_id,
            scan_id=r.scan_id,
            phase=r.phase,
            level=r.level,
            event=r.event,
            payload=r.payload,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ===========================================================================
# GET /admin/parsed-tickets/{parsed_ticket_id}  — full state
# ===========================================================================
@router.get(
    "/admin/parsed-tickets/{parsed_ticket_id}",
    dependencies=[Depends(verify_admin_key)],
)
def get_parsed_ticket_detail(
    parsed_ticket_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate one parsed ticket + linked scans + scoped audit events.

    Read-only. Returns 404 ``parsed_ticket_not_found`` when the id does
    not exist.
    """
    pt = db.execute(
        text(
            "SELECT id, receipt_id, parsed_jsonb, parsed_jsonb_hash, "
            "       raw_ticket_image_hash, ocr_engine_version, captured_at, "
            "       created_at "
            "FROM parsed_tickets WHERE id = :pt_id"
        ),
        {"pt_id": str(parsed_ticket_id)},
    ).first()
    if pt is None:
        raise HTTPException(status_code=404, detail="parsed_ticket_not_found")

    scan_rows = db.execute(
        text(
            "SELECT id, user_id, store_id, product_ean, scanned_name, price, "
            "       quantity, status, match_method, match_confidence, "
            "       rejected_reason, scanned_at, status_updated_at, "
            "       receipt_id "
            "FROM scans WHERE parsed_ticket_id = :pt_id "
            "ORDER BY scanned_at, id"
        ),
        {"pt_id": str(parsed_ticket_id)},
    ).fetchall()
    scans = [
        {
            "id": str(s.id),
            "user_id": str(s.user_id) if s.user_id else None,
            "store_id": str(s.store_id) if s.store_id else None,
            "product_ean": s.product_ean,
            "scanned_name": s.scanned_name,
            "price": s.price,
            "quantity": float(s.quantity) if s.quantity is not None else None,
            "status": s.status,
            "match_method": s.match_method,
            "match_confidence": s.match_confidence,
            "rejected_reason": s.rejected_reason,
            "scanned_at": s.scanned_at.isoformat() if s.scanned_at else None,
            "status_updated_at": (s.status_updated_at.isoformat() if s.status_updated_at else None),
            "receipt_id": str(s.receipt_id) if s.receipt_id else None,
        }
        for s in scan_rows
    ]

    scan_ids = [s["id"] for s in scans]
    audit_rows = db.execute(
        text(
            "SELECT id, parsed_ticket_id, scan_id, phase, level, event, "
            "       payload, created_at "
            "FROM pipeline_audit_log "
            "WHERE parsed_ticket_id = :pt_id "
            "   OR scan_id = ANY(CAST(:scan_ids AS uuid[])) "
            "ORDER BY created_at, id"
        ),
        {"pt_id": str(parsed_ticket_id), "scan_ids": scan_ids},
    ).fetchall()
    audit_log = [
        {
            "id": str(a.id),
            "parsed_ticket_id": (str(a.parsed_ticket_id) if a.parsed_ticket_id else None),
            "scan_id": str(a.scan_id) if a.scan_id else None,
            "phase": a.phase,
            "level": a.level,
            "event": a.event,
            "payload": a.payload,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audit_rows
    ]

    return {
        "parsed_ticket": {
            "id": str(pt.id),
            "receipt_id": str(pt.receipt_id) if pt.receipt_id else None,
            "parsed_jsonb": pt.parsed_jsonb,
            "parsed_jsonb_hash": pt.parsed_jsonb_hash,
            "raw_ticket_image_hash": pt.raw_ticket_image_hash,
            "ocr_engine_version": pt.ocr_engine_version,
            "captured_at": pt.captured_at.isoformat() if pt.captured_at else None,
            "created_at": pt.created_at.isoformat() if pt.created_at else None,
        },
        "scans": scans,
        "audit_log": audit_log,
    }


# ===========================================================================
# GET /admin/parsed-tickets  — paginated browse with derived status
# ===========================================================================
DerivedStatus = Literal["matched", "unresolved", "rejected", "mixed"]


@router.get(
    "/admin/parsed-tickets",
    dependencies=[Depends(verify_admin_key)],
)
def list_parsed_tickets(
    status: DerivedStatus | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Browse parsed tickets, optionally filtered on derived status.

    The derived status is computed from each ticket's linked scans :

    * ``matched``    — every scan ``status='matched'``
    * ``unresolved`` — every scan ``status='unresolved'``
    * ``rejected``   — every scan ``status='rejected'``
    * ``mixed``      — any other combination (incl. 0 scans)

    Implementation note : the derivation is computed in SQL via a
    ``LEFT JOIN`` + ``GROUP BY`` on ``scans``, so the filter pushes
    down to the DB. We aggregate the per-status counts and use a
    ``CASE`` to label the bucket. The 0-scan case maps to ``mixed``
    intentionally — those tickets are typically Phase-2 successes that
    failed Phase 3/4, the operator wants to see them too.

    Ordering : ``created_at DESC`` so the most recent tickets surface
    first (typical "what just dropped ?" workflow).
    """
    # Subquery counts per status. NULLIF/SUM(CASE) keeps everything in
    # one scan over `scans` per parsed_ticket.
    inner = (
        "SELECT pt.id AS pt_id, "
        "       pt.receipt_id AS receipt_id, "
        "       pt.parsed_jsonb_hash AS parsed_jsonb_hash, "
        "       pt.created_at AS created_at, "
        "       COUNT(s.id) AS scan_count, "
        "       SUM(CASE WHEN s.status = 'matched' THEN 1 ELSE 0 END) AS n_matched, "
        "       SUM(CASE WHEN s.status = 'unresolved' THEN 1 ELSE 0 END) AS n_unresolved, "
        "       SUM(CASE WHEN s.status = 'rejected' THEN 1 ELSE 0 END) AS n_rejected "
        "FROM parsed_tickets pt "
        "LEFT JOIN scans s ON s.parsed_ticket_id = pt.id "
    )
    where_clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if since is not None:
        where_clauses.append("pt.created_at >= :since")
        params["since"] = since
    inner_where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    inner_sql = inner + inner_where + " GROUP BY pt.id, pt.receipt_id, pt.parsed_jsonb_hash, pt.created_at"

    # Wrap with the status-derivation CASE + optional outer status filter.
    derive_case = (
        "CASE "
        "  WHEN scan_count > 0 AND n_matched = scan_count THEN 'matched' "
        "  WHEN scan_count > 0 AND n_unresolved = scan_count THEN 'unresolved' "
        "  WHEN scan_count > 0 AND n_rejected = scan_count THEN 'rejected' "
        "  ELSE 'mixed' "
        "END"
    )
    # PostgreSQL forbids referencing a SELECT-list alias in WHERE, so we
    # wrap the derivation in an outer subquery before filtering. Cleaner
    # than repeating the CASE in the predicate (which would also drift
    # from the projected value if either side gets edited later).
    derived_sql = (
        f"SELECT pt_id, receipt_id, parsed_jsonb_hash, created_at, "  # noqa: S608
        f"       scan_count, n_matched, n_unresolved, n_rejected, "
        f"       {derive_case} AS derived_status "
        f"FROM ({inner_sql}) inner_q"
    )
    outer_where = ""
    if status is not None:
        outer_where = "WHERE derived_status = :status_filter "
        params["status_filter"] = status

    sql = (
        f"SELECT pt_id, receipt_id, parsed_jsonb_hash, created_at, "  # noqa: S608
        f"       scan_count, n_matched, n_unresolved, n_rejected, derived_status "
        f"FROM ({derived_sql}) derived_q "
        f"{outer_where}"
        f"ORDER BY created_at DESC, pt_id DESC "
        f"LIMIT :limit OFFSET :offset"
    )
    rows = db.execute(text(sql), params).fetchall()
    items = [
        {
            "id": str(r.pt_id),
            "receipt_id": str(r.receipt_id) if r.receipt_id else None,
            "parsed_jsonb_hash": r.parsed_jsonb_hash,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "scan_count": int(r.scan_count or 0),
            "n_matched": int(r.n_matched or 0),
            "n_unresolved": int(r.n_unresolved or 0),
            "n_rejected": int(r.n_rejected or 0),
            "derived_status": r.derived_status,
        }
        for r in rows
    ]
    return {"items": items, "limit": limit, "offset": offset}


# ===========================================================================
# POST /admin/parsed-tickets/{parsed_ticket_id}/replay  — async dispatch
# ===========================================================================
class ReplayResponse(BaseModel):
    task_id: str
    parsed_ticket_id: uuid.UUID
    log_level: str


@router.post(
    "/admin/parsed-tickets/{parsed_ticket_id}/replay",
    dependencies=[Depends(verify_admin_key)],
    response_model=ReplayResponse,
)
def trigger_replay(
    parsed_ticket_id: uuid.UUID,
    log_level: str = Query(default="verbose"),
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> ReplayResponse:
    """Dispatch async re-run of Phase 3 + 4 on the persisted ParsedTicket.

    Returns immediately with the Celery ``task_id`` so the caller can
    poll ``GET /admin/tasks/{task_id}/status``.

    Errors :
        - 400 ``operator_required`` — missing X-Admin-Operator header
        - 400 ``invalid_log_level`` — not in {verbose, normal, production}
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``parsed_ticket_not_found``
    """
    operator = _require_operator(x_admin_operator)
    if log_level not in _ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail="invalid_log_level")

    # Existence check — fail fast with 404 rather than dispatching a task
    # that will silently no-op against an unknown id.
    pt = db.execute(
        text("SELECT 1 FROM parsed_tickets WHERE id = :pt_id"),
        {"pt_id": str(parsed_ticket_id)},
    ).first()
    if pt is None:
        raise HTTPException(status_code=404, detail="parsed_ticket_not_found")

    task = _dispatch_replay_task(
        parsed_ticket_id=parsed_ticket_id,
        admin_operator=operator,
        log_level=log_level,
    )
    return ReplayResponse(
        task_id=str(task.id),
        parsed_ticket_id=parsed_ticket_id,
        log_level=log_level,
    )


# ===========================================================================
# GET /admin/tasks/{task_id}/status  — Celery polling
# ===========================================================================
class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["pending", "started", "success", "failure"]
    result: dict[str, Any] | None = Field(default=None)
    error: str | None = Field(default=None)


# Module-level indirection so tests can stub the Celery AsyncResult lookup
# without touching the broker / result backend.
def _get_async_result(task_id: str):
    """Return a Celery ``AsyncResult`` for the given task id.

    Late import — keeps Celery out of the FastAPI startup path when
    unused (the result backend is configured globally on ``celery_app``).
    """
    from celery_app import celery_app

    return celery_app.AsyncResult(task_id)


# Map Celery's internal state strings to our snake-case enum.
# Celery uses : PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED.
# We collapse RETRY/REVOKED into failure (the operator's mental model
# is "did it complete OK or not"), and STARTED→started so progress is
# visible during long replays.
_CELERY_STATE_MAP: dict[str, Literal["pending", "started", "success", "failure"]] = {
    "PENDING": "pending",
    "STARTED": "started",
    "SUCCESS": "success",
    "FAILURE": "failure",
    "RETRY": "failure",
    "REVOKED": "failure",
}


@router.get(
    "/admin/tasks/{task_id}/status",
    dependencies=[Depends(verify_admin_key)],
    response_model=TaskStatusResponse,
)
def get_task_status(task_id: str) -> TaskStatusResponse:
    """Poll the status of a previously-dispatched Celery task.

    With the Celery default broker (no result backend), unknown task
    ids return ``status='pending'`` indistinguishably from queued
    tasks ; this is acceptable for the operator workflow (a stale task
    id eventually times out from the operator's UI). When a result
    backend is configured, ``SUCCESS``/``FAILURE`` carry the task's
    return value or exception repr.
    """
    async_result = _get_async_result(task_id)
    raw_state = async_result.state
    mapped = _CELERY_STATE_MAP.get(raw_state, "pending")

    result_payload: dict[str, Any] | None = None
    error_msg: str | None = None
    if mapped == "success":
        try:
            value = async_result.result
            if isinstance(value, dict):
                result_payload = value
            elif value is not None:
                # Wrap non-dict returns so the response shape stays stable.
                result_payload = {"value": value}
        except Exception:
            logger.warning("AsyncResult.result raised for %s", task_id, exc_info=True)
    elif mapped == "failure":
        try:
            err = async_result.result
            if isinstance(err, BaseException):
                error_msg = f"{type(err).__name__}: {err}"
            elif err is not None:
                error_msg = str(err)
        except Exception:
            logger.warning("AsyncResult.result raised for %s", task_id, exc_info=True)

    return TaskStatusResponse(
        task_id=task_id,
        status=mapped,
        result=result_payload,
        error=error_msg,
    )
