"""add label_image_expires_at to scans

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-04-15 20:00:00.000000
"""
from alembic import op


revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE scans ADD COLUMN label_image_expires_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE scans DROP COLUMN IF EXISTS label_image_expires_at"
    )
