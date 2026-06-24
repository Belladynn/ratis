"""scan_debug — alpha debug instrumentation table.

Revision ID: 20260427_1200_scan_debug
Revises: 20260427_1000_dismissal
Create Date: 2026-04-27 12:00:00.000000+00:00

PR #126 — alpha debug instrumentation. Stores everything needed to debug a
scan a posteriori : rich OCR blocks, optional LLM filter output, legacy
parser ReceiptData, per-pass OCR metrics, and the R2 key of the
post-preprocessing image (what PaddleOCR actually saw).

Lifecycle :
  - One row per receipt scan, written by process_receipt when STORE_DEBUG=true
  - Purged after 48h (purge_after column, indexed) by ratis_batch_purge
  - ON DELETE CASCADE from scans.id : if the source scan disappears, debug
    data goes with it.

Note : scans is the source-of-truth, but we deliberately key off scans.id
(not receipts.id) because a single receipt can produce many scan rows ;
debug data is always attached to ONE scan row (the first/canonical one
produced by the pipeline). For now we accept a single debug row per
receipt-task run by storing only when the worker writes one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260427_1200_scan_debug"
down_revision = "20260427_1000_dismissal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_debug",
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rich_blocks", postgresql.JSONB, nullable=True),
        sa.Column("llm_output", postgresql.JSONB, nullable=True),
        sa.Column("legacy_receipt_data", postgresql.JSONB, nullable=True),
        sa.Column("ocr_passes_summary", postgresql.JSONB, nullable=True),
        sa.Column("processed_image_r2_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "purge_after",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_scan_debug_purge_after",
        "scan_debug",
        ["purge_after"],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_scan_debug_purge_after")
    op.drop_table("scan_debug")
