"""Admin OCR-knowledge curation endpoints — ARCH_admin_endpoints.md PR9.

Two endpoints :

- ``GET   /api/v1/admin/knowledge/ocr-queue`` — paginated read of the
  manual-correction queue : ``ocr_knowledge`` rows where
  ``corrected IS NULL`` and ``type = 'product_name'``, ordered by
  descending ``seen_count`` (highest-impact first). ``ADMIN_API_KEY``
  only.

- ``PATCH /api/v1/admin/knowledge/{ocr_knowledge_id}`` — apply a manual
  correction (``corrected="<canonical>"``) or a dismissal
  (``corrected=null``). ``ADMIN_API_KEY`` + ``X-Admin-Operator``. The
  mutation is logged into ``pipeline_audit_log`` with
  ``phase='manual'`` so the lineage UI surfaces it next to the
  pipeline events for the same operator.

product_knowledge note (BLOCKER)
--------------------------------
The brief PR9 also mentions ``GET /admin/knowledge/product-queue`` and
``PATCH /admin/product-knowledge/{id}`` mapping ``normalized_label →
ean``. That table does not exist in the schema yet — the orchestrator
(``worker.pipeline.orchestrator._make_product_knowledge_loader``)
documents it as "post-bloc-7, returns ``None`` for now". Shipping
endpoints against a non-existent table would fail at runtime with a
``relation "product_knowledge" does not exist`` 500. We deliberately
ship only the OCR-queue half. Orchestrator owns the
DECISIONS_PENDING entry to prioritize the table migration before the
second pair of endpoints lands.

Auth pattern (mirrors PR3 ``scans.py`` / PR4 ``observability.py``) :

* ``ADMIN_API_KEY`` on every endpoint (Bearer header).
* ``X-Admin-Operator`` on the PATCH only — the GET is an inert read.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from services.knowledge_admin_service import (
    MAX_LIMIT,
    OcrKnowledgeNotFound,
    apply_ocr_correction,
    list_ocr_queue,
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
    ``pipeline_audit_log`` for human traceability per
    ARCH_admin_endpoints § Auth.
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

    Mirrors :func:`routes.admin.scans._audit` — best-effort, swallows
    failures so a broken audit row never masks the operator's actual
    mutation. ``scan_id`` and ``parsed_ticket_id`` are both NULL : an
    OCR-knowledge edit is a knowledge-table mutation, not scoped to a
    pipeline run, so the audit row stands alone (filterable by
    ``phase='manual'`` + ``event='admin_ocr_knowledge_*'``).
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


# ===========================================================================
# Response models
# ===========================================================================
class OcrQueueItem(BaseModel):
    """One row of the OCR-knowledge curation queue."""

    id: uuid.UUID
    raw_ocr: str
    seen_count: int
    created_at: Any  # serialised as ISO-8601 by FastAPI's default encoder


class OcrCorrectionResponse(BaseModel):
    """Response of the PATCH endpoint — reflects the post-update state."""

    id: uuid.UUID
    raw_ocr: str
    corrected: str | None
    source: str
    seen_count: int
    created_at: Any
    previous_corrected: str | None = Field(
        default=None,
        description="The value of ``corrected`` BEFORE this PATCH — null when "
        "the row was previously unresolved (the typical case).",
    )


# ===========================================================================
# Request models
# ===========================================================================
class OcrCorrectionRequest(BaseModel):
    """Body for ``PATCH /admin/knowledge/{id}``.

    ``corrected`` is the only field. Distinguishes :
        - ``"<canonical>"`` — apply correction
        - ``null`` — dismissal (no clean canonical exists)

    The field is REQUIRED to be present in the JSON body (no implicit
    default) so the operator's intent is unambiguous : a missing key
    would be silently treated as a dismissal, which we want to forbid.
    Pydantic v2 enforces presence by declaring no default + setting
    ``model_config = {"extra": "forbid"}`` — sending an empty body
    triggers a 422 with a clear ``field required`` message.
    """

    corrected: str | None

    model_config = {"extra": "forbid"}


# ===========================================================================
# GET /admin/knowledge/ocr-queue
# ===========================================================================
@router.get(
    "/admin/knowledge/ocr-queue",
    dependencies=[Depends(verify_admin_key)],
    response_model=list[OcrQueueItem],
)
def get_ocr_queue(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[OcrQueueItem]:
    """Read the OCR-knowledge curation queue.

    Returns rows where ``corrected IS NULL`` and ``type = 'product_name'``
    ordered by descending ``seen_count`` so the operator tackles the
    highest-impact fragments first. Pagination via ``limit`` / ``offset``
    — capped at 500 to stop accidental "give me everything" scans.

    Errors :
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 422 — out-of-range ``limit`` / ``offset`` (FastAPI default)
    """
    items = list_ocr_queue(db, limit=limit, offset=offset)
    return [
        OcrQueueItem(
            id=item.id,
            raw_ocr=item.raw_ocr,
            seen_count=item.seen_count,
            created_at=item.created_at,
        )
        for item in items
    ]


# ===========================================================================
# PATCH /admin/knowledge/{ocr_knowledge_id}
# ===========================================================================
@router.patch(
    "/admin/knowledge/{ocr_knowledge_id}",
    dependencies=[Depends(verify_admin_key)],
    response_model=OcrCorrectionResponse,
)
def patch_ocr_knowledge(
    ocr_knowledge_id: uuid.UUID,
    body: OcrCorrectionRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> OcrCorrectionResponse:
    """Apply a manual correction or a dismissal on one ``ocr_knowledge`` row.

    Body :
        ``{"corrected": "<canonical>"}`` — apply the correction
        ``{"corrected": null}`` — dismissal (the operator confirms no
        canonical text exists for this fragment ; future pipeline runs
        will keep skipping it).

    The mutation flips ``source`` to ``'manual'`` so the operator's edit
    overrides whatever auto-mechanism originally enrolled the row. An
    audit row at ``phase='manual'`` with event
    ``admin_ocr_knowledge_correction`` (or ``..._dismissal``) records
    the diff + the operator handle.

    Errors :
        - 400 ``operator_required`` — missing X-Admin-Operator header
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``ocr_knowledge_not_found``
        - 422 — Pydantic validation (missing ``corrected`` key, extra
          fields, wrong type)
    """
    operator = _require_operator(x_admin_operator)

    try:
        result = apply_ocr_correction(
            db,
            ocr_knowledge_id=ocr_knowledge_id,
            corrected=body.corrected,
            operator=operator,
        )
    except OcrKnowledgeNotFound:
        raise HTTPException(status_code=404, detail="ocr_knowledge_not_found")

    # Distinguish the audit event so a downstream "show me all dismissals"
    # filter doesn't have to inspect ``payload.diff.corrected``. Both
    # events live at ``phase='manual'`` + ``level='normal'``.
    is_dismissal = result["corrected"] is None
    event = "admin_ocr_knowledge_dismissal" if is_dismissal else "admin_ocr_knowledge_correction"

    _audit(
        db,
        event=event,
        payload={
            "operator": operator,
            "ocr_knowledge_id": str(ocr_knowledge_id),
            "raw_ocr": result["raw_ocr"],
            "diff": {
                "corrected": {
                    "from": result["previous_corrected"],
                    "to": result["corrected"],
                },
            },
        },
    )

    db.commit()
    return OcrCorrectionResponse(
        id=result["id"],
        raw_ocr=result["raw_ocr"],
        corrected=result["corrected"],
        source=result["source"],
        seen_count=result["seen_count"],
        created_at=result["created_at"],
        previous_corrected=result["previous_corrected"],
    )
