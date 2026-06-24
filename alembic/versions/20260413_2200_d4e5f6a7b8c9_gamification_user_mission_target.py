"""gamification — add target_count to user_missions for per-user boost tracking

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-13 22:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # target_count on user_missions: initialized from missions.target_count
    # (per-user because boosts double it independently per player)
    op.add_column(
        "user_missions",
        sa.Column("target_count", sa.Integer(), nullable=True),
    )
    # Backfill existing rows from the catalogue
    op.execute(
        "UPDATE user_missions um "
        "SET target_count = m.target_count "
        "FROM missions m WHERE m.id = um.mission_id"
    )
    op.alter_column("user_missions", "target_count", nullable=False)


def downgrade() -> None:
    op.drop_column("user_missions", "target_count")
