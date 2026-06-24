"""scan_debug.legacy_receipt_data → final_receipt_data + new legacy_parser_output (Phase 2e).

Revision ID: 20260428_1300_scan_debug_v2
Revises: 20260428_1100_match_llm
Create Date: 2026-04-28 13:00:00.000000+00:00

ARCH OCR↔LLM Bridge v2 (Phase 2e) — the existing
``scan_debug.legacy_receipt_data`` column was misnamed. It actually
holds the ``ReceiptData`` *used* to create the scan (= LLM output
converted, or legacy fallback when the LLM was disabled / crashed),
NOT the legacy parser's own output. Rename to ``final_receipt_data``
to reflect its real semantics.

In addition, introduce ``legacy_parser_output`` so the worker can run
``parse_receipt`` in parallel and persist its result side-by-side
for the debug viewer (real comparison rather than the previous single
ambiguous field).

Idempotent : both operations are conditional on column existence so
the migration is safe to re-run.
"""
from __future__ import annotations

from alembic import op


revision = "20260428_1300_scan_debug_v2"
down_revision = "20260428_1100_match_llm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename legacy_receipt_data → final_receipt_data (idempotent).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='scan_debug' AND column_name='legacy_receipt_data'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='scan_debug' AND column_name='final_receipt_data'
            ) THEN
                ALTER TABLE scan_debug
                    RENAME COLUMN legacy_receipt_data TO final_receipt_data;
            END IF;
        END $$;
        """
    )
    # 2. Add new legacy_parser_output column for the parallel run.
    op.execute(
        "ALTER TABLE scan_debug ADD COLUMN IF NOT EXISTS "
        "legacy_parser_output JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scan_debug DROP COLUMN IF EXISTS legacy_parser_output")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='scan_debug' AND column_name='final_receipt_data'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='scan_debug' AND column_name='legacy_receipt_data'
            ) THEN
                ALTER TABLE scan_debug
                    RENAME COLUMN final_receipt_data TO legacy_receipt_data;
            END IF;
        END $$;
        """
    )
