"""add timezone to users

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-04-08 17:00:00.000000

Stores the user's IANA timezone (sent by the app at register or first scan).
Default "Europe/Paris" for V1. Used by ratis_notifier to compute quiet hours
in the user's local time.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Europe/Paris"),
    )


def downgrade() -> None:
    op.drop_column("users", "timezone")
