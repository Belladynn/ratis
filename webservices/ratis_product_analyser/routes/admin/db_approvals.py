"""Admin endpoint — registration of DB write proposals reaching the human gate (SP6).

Two endpoints :

- ``POST /api/v1/admin/db-approvals`` — the n8n ``db-write-pipeline``
  workflow registers a proposal that has cleared the machine gates and
  reached the human approval gate. The row is INSERTed into
  ``db_write_approvals`` in status ``pending`` ; the operator decides
  later from the admin UI ``/admin/ui/db-approvals``.
- ``POST /api/v1/admin/db-approvals/{submission_id}/expire`` — the n8n
  ``Wait`` timeout branch marks a still-``pending`` proposal ``expired``
  after 24 h with no decision. Idempotent (no-op if already decided).

Auth : Bearer ``ADMIN_API_KEY`` (machine call n8n→PA, consistent with
the rest of the ``/api/v1/admin/*`` family). No ``X-Admin-Operator`` —
the operator handle is bound at decision time by the UI session, not at
registration time.

See ``docs/superpowers/specs/2026-05-18-db-approval-ui-sp6-design.md``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.models.db_write_approval import DbWriteApproval, DbWriteApprovalStatus
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# All endpoints here are machine-call-only (n8n → PA) — router-level ADMIN_API_KEY is intentional.
# If you add a human-operator endpoint with different auth, move it to a separate router.
router = APIRouter(dependencies=[Depends(verify_admin_key)])


class RegisterApprovalRequest(BaseModel):
    """Payload POSTed by the n8n ``Register approval`` node.

    ``payload`` is the opaque proposal envelope — procedure, args,
    rationale, dry-run checks, LLM feedback, ``client_message``,
    ``investigation``, flags. The endpoint stores it as-is ; the UI
    detail page reads named keys out of it.
    """

    submission_id: uuid.UUID
    payload: dict[str, Any]
    touches_money_tables: bool = False
    llm_unavailable: bool = False
    resume_url: str = Field(min_length=1)


@router.post("/admin/db-approvals")
def register_db_approval(
    body: RegisterApprovalRequest,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Register a proposal reaching the human gate — INSERT a ``pending`` row."""
    row = DbWriteApproval(
        submission_id=body.submission_id,
        payload=body.payload,
        touches_money_tables=body.touches_money_tables,
        llm_unavailable=body.llm_unavailable,
        resume_url=body.resume_url,
    )
    db.add(row)
    db.commit()  # MANDATORY — no commit = silent rollback prod (R02)
    logger.info("db-approval registered: submission_id=%s", body.submission_id)
    return {"status": "registered", "submission_id": str(body.submission_id)}


@router.post("/admin/db-approvals/{submission_id}/expire")
def expire_db_approval(
    submission_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Mark a still-pending proposal as expired — n8n ``Wait`` timeout branch.

    Idempotent : a row already decided (approved/rejected/expired) is a
    no-op. 404 if the submission_id is unknown.
    """
    row = db.query(DbWriteApproval).filter_by(submission_id=submission_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="db_approval_not_found")
    if row.status != DbWriteApprovalStatus.PENDING:
        return {"status": "noop", "submission_id": str(submission_id)}
    row.status = DbWriteApprovalStatus.EXPIRED
    row.decided_at = datetime.now(UTC)
    db.commit()  # MANDATORY — no commit = silent rollback prod (R02)
    logger.info("db-approval expired: submission_id=%s", submission_id)
    return {"status": "expired", "submission_id": str(submission_id)}
