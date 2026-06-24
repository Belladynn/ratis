"""SQLAlchemy ORM models for the pipeline DB tables.

Created in bloc 2 of ``ARCH_receipt_pipeline.md``. Pure mapping —
no business logic. The Pydantic equivalents live in
``webservices/ratis_product_analyser/worker/pipeline/types.py``
(immutable / frozen contracts shared between phases).

Tables :

- ``parsed_tickets`` — immutable Phase-2 state (Cardinal state).
  ``parsed_jsonb_hash`` is UNIQUE → idempotent persistence.
- ``pipeline_audit_log`` — append-only event log per Phase / level.
  An UPDATE-blocking trigger lives in the migration ; this model
  intentionally does NOT redeclare it (it is a SQL-only enforcement,
  not part of the ORM contract).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


# ============================================================
# PARSED TICKETS
# ============================================================
class ParsedTicket(Base):
    """Cardinal state of Phase 2 — the immutable post-comprehend snapshot.

    Mirrors ``pipeline.types.ParsedTicket``. ``parsed_jsonb`` stores the
    canonical JSON dump used to compute ``parsed_jsonb_hash`` (sha256 hex,
    UNIQUE). Re-running Phase 2 on the same image yields the same hash and
    upserts as a no-op (idempotence per ARCH § Cardinal state).
    """

    __tablename__ = "parsed_tickets"
    __table_args__ = (UniqueConstraint("parsed_jsonb_hash", name="uq_parsed_tickets_jsonb_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Receipt may not exist yet at Phase 2 time (created at Phase 4) — kept
    # nullable + ON DELETE CASCADE so deleting a receipt removes its tickets.
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="CASCADE"),
        nullable=True,
    )
    parsed_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    parsed_jsonb_hash: Mapped[str] = mapped_column(Text, nullable=False)
    raw_ticket_image_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# Indexes are declared as standalone Index objects so they reach
# Base.metadata.create_all() in tests without needing to be re-declared
# in the migration body itself (single source of truth in the migration).
Index("ix_parsed_tickets_receipt_id", ParsedTicket.receipt_id)
Index("ix_parsed_tickets_image_hash", ParsedTicket.raw_ticket_image_hash)
Index("ix_parsed_tickets_created_at", ParsedTicket.created_at)


# ============================================================
# PIPELINE AUDIT LOG
# ============================================================
class PipelineAuditLog(Base):
    """Append-only structured event log for pipeline phases.

    ``phase`` ∈ {'extract', 'comprehend', 'match', 'persist'} (the 4 phases
    of the ARCH). ``level`` controls verbosity (verbose < normal < production).
    UPDATE is blocked by a trigger ``trg_pipeline_audit_log_no_update`` defined
    in the migration ; DELETE is allowed for retention / admin cleanup.
    """

    __tablename__ = "pipeline_audit_log"
    __table_args__ = (
        # 'manual' added in migration 20260430_1700_paadmin for admin-
        # originated events (ARCH_admin_endpoints PR3 — scan override +
        # replay-match) alongside the four pipeline phases.
        CheckConstraint(
            "phase IN ('extract', 'comprehend', 'match', 'persist', 'manual')",
            name="ck_pipeline_audit_log_phase",
        ),
        CheckConstraint(
            "level IN ('verbose', 'normal', 'production')",
            name="ck_pipeline_audit_log_level",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    parsed_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parsed_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("ix_pipeline_audit_log_parsed_ticket_id", PipelineAuditLog.parsed_ticket_id)
Index("ix_pipeline_audit_log_scan_id", PipelineAuditLog.scan_id)
Index("ix_pipeline_audit_log_created_at", PipelineAuditLog.created_at)
Index(
    "ix_pipeline_audit_log_phase_event",
    PipelineAuditLog.phase,
    PipelineAuditLog.event,
)
