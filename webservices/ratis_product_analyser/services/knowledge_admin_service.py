"""Admin service layer for OCR knowledge curation — ARCH_admin_endpoints PR9.

This service powers the manual-curation queue described in
``TRAINING.md`` § ``product_knowledge`` (renamed to ``ocr_knowledge``
by migration 20260415_2300). The pipeline auto-enrolls every raw OCR
fragment with ``corrected=NULL`` ; an operator opens the queue, applies
a correction (or dismisses the row when no clean canonical exists) and
the next pipeline run picks the new mapping up via
``ratis_core.knowledge`` lookups.

Two operations are exposed :

- :func:`list_ocr_queue` — paginated read of unresolved rows
  (``corrected IS NULL``) ordered by descending ``seen_count`` so the
  operator tackles the highest-impact fragments first.
- :func:`apply_ocr_correction` — write a correction or dismissal
  (``corrected = None``). Stamps ``source='manual'`` on every operator
  edit so ``ck_ocr_knowledge_source`` stays satisfied and the audit
  trail preserves provenance.

Layer split (R03) : the FastAPI route stays thin (auth + I/O shape)
and delegates DB mutation here. No HTTP exceptions in this module —
404 / 422 surface via dedicated exceptions translated upstream.

Dismissal vs correction
-----------------------
The brief calls out two distinct operator outcomes :

- ``corrected="<canonical>"`` — operator fixes the OCR mistake.
  Pipeline lookups will rewrite future raw_ocr → corrected.
- ``corrected=None`` — operator marks the fragment as "no clean
  canonical exists". Pipeline lookups will skip the raw_ocr instead
  of repeatedly surfacing it for curation. Concretely the row keeps
  ``corrected=NULL`` but its ``source`` flips from
  ``ocr_arbitrage`` (auto-enrolled) to ``manual`` so a future
  ``WHERE corrected IS NULL AND source = 'ocr_arbitrage'`` filter
  on the queue would naturally hide it. We honor this convention
  for forward-compat — the GET filter today only checks
  ``corrected IS NULL``, but a follow-up "hide dismissals" toggle
  is cheap once ``source = 'manual'`` is the marker.

product_knowledge note (BLOCKER)
--------------------------------
The brief PR9 also asks for a ``/admin/product-knowledge/{id}``
queue mapping ``normalized_label → ean``. That table is not in the
schema yet — the orchestrator
(:func:`worker.pipeline.orchestrator._make_product_knowledge_loader`)
explicitly documents it as "post-bloc-7, returns ``None`` for now".
This service intentionally ships only the OCR-queue half so we don't
introduce dead endpoints against a non-existent table. See
DECISIONS_PENDING.md (orchestrator owns the entry).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.orm import Session

# Filter applied on every queue read — only ``product_name`` rows are
# user-facing canonical product fragments. Other ``type`` values
# (brand_name / retailer_header / address_token / dismissal) are
# curated by separate workflows out of scope for PR9.
_PRODUCT_NAME_TYPE: Final[str] = "product_name"


# Sentinel value used as ``source`` when an operator edits a row. We
# deliberately do NOT preserve the prior source : the operator's edit is
# now the canonical truth regardless of how the row originally landed.
_OPERATOR_SOURCE: Final[str] = "manual"


# Pagination caps. Mirror the ``Query(le=500, ge=1)`` constraints
# enforced by the route layer so a direct service-level call from a
# future internal job picks up the same bounds.
DEFAULT_LIMIT: Final[int] = 50
MAX_LIMIT: Final[int] = 500


# ---------------------------------------------------------------------------
# Exceptions — translated by the route layer into HTTPException
# (KP-05 : never raise HTTPException in services).
# ---------------------------------------------------------------------------
class OcrKnowledgeNotFound(Exception):
    """Raised when a PATCH targets an ``ocr_knowledge.id`` that doesn't exist."""


# ---------------------------------------------------------------------------
# DTOs — exposed to the route as plain dataclasses so the route only
# needs to call ``__dict__`` / model_validate to shape the JSON. Keeping
# these as Pydantic models would couple the service to FastAPI ; a
# dataclass is sufficient for the typed read.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OcrKnowledgeQueueItem:
    """One row of the OCR-knowledge curation queue."""

    id: uuid.UUID
    raw_ocr: str
    seen_count: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_ocr_queue(
    db: Session,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[OcrKnowledgeQueueItem]:
    """Return the OCR-knowledge curation queue, highest-impact first.

    Filters :
        - ``corrected IS NULL`` — only unresolved rows
        - ``type = 'product_name'`` — out-of-scope categories
          (brand_name / retailer_header / address_token / dismissal) are
          curated by other workflows.

    Ordering : ``seen_count DESC, created_at ASC, id ASC`` — frequency
    drives the priority, ties break by oldest-first so a 1-shot fragment
    seen weeks ago doesn't leapfrog a fresh duplicate. Stable secondary
    keys keep pagination consistent across repeated queries with the same
    ``offset``.
    """
    rows = db.execute(
        text(
            "SELECT id, raw_ocr, seen_count, created_at "
            "FROM ocr_knowledge "
            "WHERE corrected IS NULL "
            "  AND type = :type "
            "ORDER BY seen_count DESC, created_at ASC, id ASC "
            "LIMIT :limit OFFSET :offset"
        ),
        {
            "type": _PRODUCT_NAME_TYPE,
            "limit": limit,
            "offset": offset,
        },
    ).fetchall()
    return [
        OcrKnowledgeQueueItem(
            id=r.id,
            raw_ocr=r.raw_ocr,
            seen_count=r.seen_count,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def apply_ocr_correction(
    db: Session,
    *,
    ocr_knowledge_id: uuid.UUID,
    corrected: str | None,
    operator: str,
) -> dict:
    """Write a correction or dismissal on one ``ocr_knowledge`` row.

    Args :
        db : SQLAlchemy session — caller commits.
        ocr_knowledge_id : target row id.
        corrected : the canonical text (any non-empty string) OR
            ``None`` for a dismissal. The route validates non-empty
            already ; we treat empty/whitespace-only as ``None`` for
            defense in depth (a UI bug that submits ``""`` is exactly
            what a dismissal looks like, never a real correction).
        operator : ``X-Admin-Operator`` handle — propagated upstream
            into the audit log by the route. Service stays I/O-pure
            for the audit decision (which payload, which event).

    Side effects :
        - UPDATE the row's ``corrected`` + ``source = 'manual'``.
        - Bump the implicit "this row was curated" signal by stamping
          ``source = 'manual'`` even on a dismissal so a follow-up
          query can distinguish a never-curated NULL row from an
          operator-acknowledged NULL.

    Raises :
        :class:`OcrKnowledgeNotFound` — when the id has no row.

    Returns :
        Dict with the updated fields, ready for the route response :
        ``{id, raw_ocr, corrected, source, seen_count, created_at}``.
        Includes the diff-relevant ``previous_corrected`` so the route
        can attach it to the audit payload without a re-SELECT.
    """
    # Normalize : whitespace-only → None (treat as dismissal). The route
    # rejects None-vs-string ambiguity at the Pydantic layer ; this is
    # a guard against a stray UI sending ``"   "`` as a correction.
    normalized = corrected.strip() if isinstance(corrected, str) else None
    if normalized == "":
        normalized = None

    # SELECT-then-UPDATE in one transaction — caller's session is
    # already inside a transaction (FastAPI request-scope). We don't
    # ``with_for_update()`` because the operator workflow is single
    # writer per row in practice ; if two operators race, last-write
    # wins is acceptable (both edits funnel through audit).
    current = db.execute(
        text("SELECT id, raw_ocr, corrected, seen_count, created_at FROM ocr_knowledge WHERE id = :id"),
        {"id": str(ocr_knowledge_id)},
    ).first()
    if current is None:
        raise OcrKnowledgeNotFound(str(ocr_knowledge_id))

    db.execute(
        text("UPDATE ocr_knowledge SET   corrected = :corrected,   source = :source WHERE id = :id"),
        {
            "id": str(ocr_knowledge_id),
            "corrected": normalized,
            "source": _OPERATOR_SOURCE,
        },
    )

    return {
        "id": current.id,
        "raw_ocr": current.raw_ocr,
        "corrected": normalized,
        "source": _OPERATOR_SOURCE,
        "seen_count": current.seen_count,
        "created_at": current.created_at,
        "previous_corrected": current.corrected,
        # ``operator`` is reflected back so the route can build the
        # audit payload without re-passing it.
        "operator": operator,
    }
