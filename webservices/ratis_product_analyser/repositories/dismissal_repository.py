"""Repository for the LLM dismissal feedback loop (Stage 1 read side).

Reads ``ocr_knowledge`` rows of type='dismissal' so Stage 1 (local
pre-filter) can drop blocks already classified as boilerplate before
the LLM ever runs.

Storage choice (decision recorded in PR #122) : the dismissal feedback
extends the existing ``ocr_knowledge`` table rather than creating a
sibling. Rationale :
- ``raw_ocr`` already carries OCR text — perfect for the dismissal text.
- ``seen_count`` already tracks occurrences — exactly what we need.
- Type partitioning (``type='dismissal'``) keeps queries scoped.

Phase 2h note : the v1 ``bulk_upsert_dismissals`` writer was retired
together with the v1 ``filter_and_learn`` flow. The v2 worker writes
dismissals directly via ``_persist_llm_knowledge_v2`` (with similarity
guard upstream so hallucinations never reach the cache). This module
keeps only the read side, used by ``local_prefilter.local_classify_blocks``.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_known_dismissals(db: Session) -> set[str]:
    """Return the set of all dismissal texts currently in DB, normalized.

    Used by ``local_prefilter`` to strip blocks already known as
    boilerplate before sending the receipt to the LLM. Saves tokens,
    accelerates the learning loop."""
    rows = db.execute(text("SELECT raw_ocr FROM ocr_knowledge WHERE type = 'dismissal'")).fetchall()
    return {row[0] for row in rows}


__all__ = ["get_known_dismissals"]
