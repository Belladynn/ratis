"""unknown_scans_weekly_aggregate

Revision ID: 20260421_1800_unknown_aggregate
Revises: 20260421_1305_unknown_store
Create Date: 2026-04-21 18:00:00.000000+00:00

Part B retention table. The daily purge batch rolls up label scans
older than 7 days with store_status='unknown' and then hard-deletes
them (PII on scans.user_lat / user_lng must not linger past the
reconciliation window — see DA-30).

  - ``year_week`` : ISO week string "YYYY-Www" (PK)
  - ``scan_count`` : total unknown scans purged for that week
  - ``count_per_scan_type`` : JSONB breakdown by scan_type
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260421_1800_unknown_aggregate"
down_revision = "20260421_1305_unknown_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unknown_scans_weekly_aggregate",
        sa.Column("year_week", sa.Text(), primary_key=True),
        sa.Column("scan_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "count_per_scan_type",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("unknown_scans_weekly_aggregate")
