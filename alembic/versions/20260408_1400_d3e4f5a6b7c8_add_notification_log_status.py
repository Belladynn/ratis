"""add status and expo_ticket_id to notification_logs

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-04-08 14:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_logs",
        sa.Column("status", sa.Text(), nullable=False, server_default="sent"),
    )
    op.add_column(
        "notification_logs",
        sa.Column("expo_ticket_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_logs", "expo_ticket_id")
    op.drop_column("notification_logs", "status")
