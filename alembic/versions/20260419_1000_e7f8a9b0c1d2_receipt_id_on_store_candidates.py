"""Add receipt_id to store_candidates for audit traceability.

Revision ID: e7f8a9b0c1d2
Revises: b3c4d5e6f7a8
Create Date: 2026-04-19 10:00:00.000000+00:00

store_candidates.receipt_id UUID NULL REFERENCES receipts(id) ON DELETE SET NULL
Tracks which receipt first triggered a candidate insertion — audit trail only.
Does not affect business logic (no FK in occurrence_count increment path).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7f8a9b0c1d2"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "store_candidates",
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("receipts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE store_candidates "
        "DROP CONSTRAINT IF EXISTS store_candidates_receipt_id_fkey"
    )
    op.execute("ALTER TABLE store_candidates DROP COLUMN IF EXISTS receipt_id")
