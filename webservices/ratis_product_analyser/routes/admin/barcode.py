"""Admin endpoints for receipt barcode management — pipeline PR-C.

Two endpoints :

- ``GET /api/v1/admin/barcode/unknown-retailers`` — read-only inventory
  of retailers where receipts accumulate raw barcodes but no parsed
  fields (or where the store is unresolved). Surfaces the worklist for
  the admin who configures ``retailer_receipt_formats``.

- ``POST /api/v1/admin/barcode/reparse`` — dispatch a Celery task that
  re-runs the v3 barcode parser on every receipt with
  ``receipt_barcode IS NOT NULL AND barcode_fields IS NULL`` for a given
  ``retailer_key``. Typically called immediately after a row was
  inserted into ``retailer_receipt_formats`` for that retailer.

Auth pattern (mirrors PA admin/scans.py PR3) :

* ``ADMIN_API_KEY`` on every endpoint (Bearer header).
* ``X-Admin-Operator`` (logged in ``pipeline_audit_log``) on the mutation.
* No 2FA / TOTP — these endpoints touch parser metadata, not financial
  state.

Retailer-key normalization is delegated to
:func:`worker.ocr.store_detector._normalize_retailer_key` (lowercase,
strip accents, spaces → underscores). The endpoint normalizes inputs in
Python rather than in SQL so we share one definition across the v3
barcode parser, the Celery task, and the admin queries.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# ───────────────────────────────────────────────────────────────────────────
# Models
# ───────────────────────────────────────────────────────────────────────────
class UnknownRetailerRow(BaseModel):
    """One bucket in the unknown-retailers report.

    ``retailer`` is the raw ``stores.retailer`` brand text (or ``None``
    when the store could not be resolved upstream — header OCR miss,
    user-suggested store pending validation, etc.).
    """

    retailer: str | None
    ticket_count: int
    first_seen: str
    last_seen: str


class ReparseRequest(BaseModel):
    """Payload for ``POST /admin/barcode/reparse``.

    ``retailer_key`` is the canonical (already-normalized) key — the
    same string stored in ``retailer_receipt_formats.retailer_key``.
    """

    retailer_key: str = Field(min_length=1, max_length=64)


class ReparseResponse(BaseModel):
    task_id: str
    retailer_key: str
    estimated_count: int


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────
def _require_operator(x_admin_operator: str | None) -> str:
    """Return the trimmed operator handle ; raise 400 if absent / blank.

    Honor-system identifier (no crypto check) — only logged for human
    traceability per the ARCH_admin_endpoints auth pattern.
    """
    if not x_admin_operator or not x_admin_operator.strip():
        raise HTTPException(status_code=400, detail="operator_required")
    return x_admin_operator.strip()


# Module-level indirection so tests can monkeypatch the dispatch without
# importing Celery infrastructure. In production this delegates to the
# Celery task ``.delay`` (async dispatch via Redis broker).
def _dispatch_reparse_task(*, retailer_key: str, admin_operator: str):
    """Dispatch the reparse Celery task asynchronously.

    Late import — keeps the route module light and avoids dragging the
    worker namespace (and its OCR-heavy transitive imports) into the
    FastAPI startup path.
    """
    from worker.barcode_reparse_task import reparse_barcode_for_retailer

    return reparse_barcode_for_retailer.delay(
        retailer_key=retailer_key,
        admin_operator=admin_operator,
    )


# A SQL fragment that recomputes the canonical retailer_key from
# ``stores.retailer`` using the conftest-loaded ``unaccent`` extension.
# Mirrors :func:`worker.ocr.store_detector._normalize_retailer_key`
# (lowercase → strip accents → replace spaces with underscores).
_NORMALIZE_RETAILER_SQL = "REPLACE(LOWER(unaccent(s.retailer)), ' ', '_')"


# ───────────────────────────────────────────────────────────────────────────
# GET /admin/barcode/unknown-retailers
# ───────────────────────────────────────────────────────────────────────────
@router.get(
    "/admin/barcode/unknown-retailers",
    dependencies=[Depends(verify_admin_key)],
    response_model=list[UnknownRetailerRow],
)
def list_unknown_retailers(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[UnknownRetailerRow]:
    """Return retailers with raw barcodes but no parsed ``barcode_fields``.

    Includes both :

    * Resolved retailers (``stores.retailer`` set) without a
      ``retailer_receipt_formats`` row matching the normalized key.
    * Unresolved receipts (``store_id IS NULL``) bucketed under
      ``retailer = NULL``.

    Ordered by ``ticket_count`` descending — the admin tackles the
    high-volume formats first.
    """
    # ``_NORMALIZE_RETAILER_SQL`` is a module-level constant, not user
    # input — there is no SQL-injection vector. The only bound
    # parameter (``:limit``) is passed via SQLAlchemy bind. The S608
    # suppression sits on the first line of the concatenation (where
    # ruff anchors the diagnostic).
    sql = (
        "SELECT s.retailer AS retailer, COUNT(*) AS ticket_count, "  # noqa: S608
        "       MIN(r.created_at) AS first_seen, "
        "       MAX(r.created_at) AS last_seen "
        "FROM receipts r "
        "LEFT JOIN stores s ON r.store_id = s.id "
        "WHERE r.receipt_barcode IS NOT NULL "
        "  AND r.barcode_fields IS NULL "
        "  AND (s.retailer IS NULL OR "
        "       " + _NORMALIZE_RETAILER_SQL + " NOT IN "
        "       (SELECT retailer_key FROM retailer_receipt_formats)) "
        "GROUP BY s.retailer "
        "ORDER BY ticket_count DESC, s.retailer NULLS LAST "
        "LIMIT :limit"
    )

    rows = db.execute(text(sql), {"limit": limit}).mappings().all()

    return [
        UnknownRetailerRow(
            retailer=row["retailer"],
            ticket_count=int(row["ticket_count"]),
            first_seen=row["first_seen"].isoformat(),
            last_seen=row["last_seen"].isoformat(),
        )
        for row in rows
    ]


# ───────────────────────────────────────────────────────────────────────────
# POST /admin/barcode/reparse
# ───────────────────────────────────────────────────────────────────────────
@router.post(
    "/admin/barcode/reparse",
    dependencies=[Depends(verify_admin_key)],
    response_model=ReparseResponse,
)
def trigger_reparse(
    body: ReparseRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> ReparseResponse:
    """Dispatch async re-parsing of receipts with raw barcode but no fields.

    Use case : the admin just inserted a row in
    ``retailer_receipt_formats`` for ``carrefour`` ; this endpoint kicks
    off a Celery task that parses every backlog receipt for that
    retailer. The endpoint returns immediately with a task id (the API
    is fire-and-forget — the admin polls Celery / inspects DB later).

    Errors :
        - 400 ``operator_required`` — missing / blank ``X-Admin-Operator``
        - 403 ``forbidden`` — wrong / missing ``ADMIN_API_KEY``
        - 404 ``format_not_configured`` — no ``retailer_receipt_formats``
          row for ``retailer_key`` (nothing to reparse against — the
          admin must add the format row first)
    """
    operator = _require_operator(x_admin_operator)

    fmt_exists = db.execute(
        text("SELECT 1 FROM retailer_receipt_formats WHERE retailer_key = :k"),
        {"k": body.retailer_key},
    ).first()
    if fmt_exists is None:
        raise HTTPException(status_code=404, detail="format_not_configured")

    # Estimate count for the response. The Celery task re-runs the same
    # WHERE so the admin sees how much backlog is about to be processed.
    # ``_NORMALIZE_RETAILER_SQL`` is a module-level constant ; the
    # user-supplied value is bound as ``:k``. S608 suppression is safe.
    count_sql = (
        "SELECT COUNT(*) AS c FROM receipts r "  # noqa: S608
        "LEFT JOIN stores s ON r.store_id = s.id "
        "WHERE r.receipt_barcode IS NOT NULL "
        "  AND r.barcode_fields IS NULL "
        "  AND " + _NORMALIZE_RETAILER_SQL + " = :k"
    )
    count = db.execute(text(count_sql), {"k": body.retailer_key}).scalar_one()

    task = _dispatch_reparse_task(
        retailer_key=body.retailer_key,
        admin_operator=operator,
    )

    return ReparseResponse(
        task_id=str(task.id),
        retailer_key=body.retailer_key,
        estimated_count=int(count),
    )
