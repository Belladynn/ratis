"""Add pending_items, user_store_hint on receipts; source on stores.

Revision ID: b3c4d5e6f7a8
Revises: d6e5f4a3b2c1
Create Date: 2026-04-18 12:00:00.000000+00:00

- receipts.pending_items JSONB NULL — OCR-parsed items for receipts with no
  store match. Cleared to NULL after items are promoted to real scans.
- receipts.user_store_hint TEXT NULL — raw string the user typed when
  identifying an unknown store. Audit trail only.
- stores.source TEXT NOT NULL DEFAULT 'osm' CHECK IN ('osm','admin',
  'user_suggested') — tracks how the store row was created. Audit column only,
  does not affect cashback logic.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3c4d5e6f7a8"
down_revision = "d6e5f4a3b2c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── receipts ─────────────────────────────────────────────────────────────
    op.add_column(
        "receipts",
        sa.Column(
            "pending_items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "receipts",
        sa.Column("user_store_hint", sa.Text(), nullable=True),
    )

    # ── stores ────────────────────────────────────────────────────────────────
    op.add_column(
        "stores",
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'osm'"),
        ),
    )
    op.create_check_constraint(
        "ck_stores_source",
        "stores",
        "source IN ('osm', 'admin', 'user_suggested')",
    )

    # Existing stores: rows with osm_id set came from osm_sync — already 'osm'.
    # All others also default to 'osm' (created by the system before this column).
    # The server_default handles both; no explicit UPDATE needed.


def downgrade() -> None:
    op.execute("ALTER TABLE stores DROP CONSTRAINT IF EXISTS ck_stores_source")
    op.drop_column("stores", "source")

    op.drop_column("receipts", "user_store_hint")
    op.drop_column("receipts", "pending_items")
