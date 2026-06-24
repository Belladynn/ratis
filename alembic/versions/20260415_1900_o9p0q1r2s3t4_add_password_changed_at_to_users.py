"""add password_changed_at to users

Revision ID: o9p0q1r2s3t4
Revises: m7n8o9p0q1r2
Create Date: 2026-04-15 19:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = "o9p0q1r2s3t4"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at")
