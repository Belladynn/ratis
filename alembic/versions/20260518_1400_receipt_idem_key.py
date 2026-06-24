"""Add receipts.idempotency_key for idempotent upload replay.

Revision ID: 20260518_1400_receipt_idem
Revises: 20260518_1300_acct_type
Create Date: 2026-05-18 14:00:00.000000

A scan-upload client that is killed after the POST succeeds server-side
but before it records success would otherwise re-upload on restart and
create a duplicate receipt. The client now generates a stable
``idempotency_key`` at enqueue time and sends it on every retry. The
backend detects the replay on the partial unique index and returns the
existing receipt instead of creating a new one.

``idempotency_key`` is NULL for legacy clients ; the unique index is
partial (``WHERE idempotency_key IS NOT NULL``) so they are unaffected.
Scoped per ``user_id`` so keys cannot collide across accounts.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260518_1400_receipt_idem"
down_revision = "20260518_1300_acct_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column("idempotency_key", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_receipts_user_idempotency_key",
        "receipts",
        ["user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_receipts_user_idempotency_key"
    )
    op.drop_column("receipts", "idempotency_key")
