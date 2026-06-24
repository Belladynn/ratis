"""missions — unique active (action_type, frequency)

Revision ID: f93171d19694
Revises: f2a3b4c5d6e7
Create Date: 2026-04-12 10:00:00.000000

Enforce that at most one active mission exists per (action_type, frequency).
Prevents the lazy-gen dedup algorithm from being bypassed when check_missions_progress
runs before the first GET /gamification/missions.
"""
from alembic import op

revision = "f93171d19694"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_missions_active_action_frequency "
        "ON missions (action_type, frequency) "
        "WHERE is_active = TRUE"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_missions_active_action_frequency")
