"""add missing updated_at triggers

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-04-09 10:00:00.000000

The initial schema created fn_set_updated_at() and applied BEFORE UPDATE triggers
to 17 tables. Three tables that have an updated_at column were added later without
receiving the trigger: user_cab_balance, user_cashback_balance, user_streaks.

This migration ensures all tables with an updated_at column have the trigger.
"""
from __future__ import annotations

from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

_TABLES = [
    "user_cab_balance",
    "user_cashback_balance",
    "user_streaks",
]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"""
            CREATE OR REPLACE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
        """)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
