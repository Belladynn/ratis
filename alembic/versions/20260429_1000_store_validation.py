"""store_validation_status — user-suggested store validation lifecycle.

Revision ID: 20260429_1000_storeval
Revises: 20260428_1300_scan_debug_v2
Create Date: 2026-04-29 10:00:00.000000+00:00

PR-B Phase 1 — adds the validation lifecycle for stores. ``stores.validation_status``
distinguishes a trustworthy store ('confirmed') from a fresh user-suggested one
('pending') or one stuck pending too long ('suspicious'). Cashback gating reads
this column on top of ``receipts.store_status`` (defense in depth).

Backfill caveat (P-2 in ARCH_store_validation): existing
``stores.source='user_suggested'`` rows are flipped to ``validation_status='pending'``
unconditionally. Acceptable for V1 alpha (no real prod yet). If/when run against a
populated prod DB, an audit pass on those rows is required first.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260429_1000_storeval"
down_revision = "20260428_1300_scan_debug_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New column with default 'confirmed' so existing OSM/admin rows stay valid.
    op.add_column(
        "stores",
        sa.Column(
            "validation_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'confirmed'"),
        ),
    )
    op.execute(
        "ALTER TABLE stores ADD CONSTRAINT ck_stores_validation_status "
        "CHECK (validation_status IN ('pending', 'confirmed', 'suspicious'))"
    )

    # 2. Backfill : pre-existing user_suggested rows flip to pending.
    op.execute(
        "UPDATE stores SET validation_status='pending' WHERE source='user_suggested'"
    )

    # 3. Audit : who suggested. ON DELETE SET NULL because we never want a user
    # deletion to cascade-delete stores (they may be referenced by scans/receipts
    # of other users).
    op.add_column(
        "stores",
        sa.Column(
            "suggested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 4. Partial index — the batch only ever scans pending rows.
    op.create_index(
        "idx_stores_validation_pending",
        "stores",
        ["validation_status"],
        postgresql_where=sa.text("validation_status = 'pending'"),
    )

    # 5. Audit table for transitions. Single source of truth for who/why a store
    # changed validation state. Note: column is ``meta`` (not ``metadata`` —
    # which clashes with SQLAlchemy's ``Base.metadata``).
    op.create_table(
        "store_validation_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_store_validation_history_store_id",
        "store_validation_history",
        ["store_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_store_validation_history_store_id",
        table_name="store_validation_history",
    )
    op.drop_table("store_validation_history")
    op.drop_index("idx_stores_validation_pending", table_name="stores")
    op.drop_column("stores", "suggested_by_user_id")
    op.execute(
        "ALTER TABLE stores DROP CONSTRAINT IF EXISTS ck_stores_validation_status"
    )
    op.drop_column("stores", "validation_status")
