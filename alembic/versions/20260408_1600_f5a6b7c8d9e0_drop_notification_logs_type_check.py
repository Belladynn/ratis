"""drop notification_logs_type_check constraint

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-04-08 16:00:00.000000

The type CHECK constraint was defined in the initial schema with legacy values
(price_drop, streak_reminder, …). ratis_notifier uses different types
(scan_done, cashback_available, badge_unlocked, price_alert), causing
CHECK violations on every INSERT in production.
"""
from __future__ import annotations

from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS: constraint only existed on DBs initialized before the initial schema
    # was cleaned up. Fresh CI environments never had it.
    op.execute(
        "ALTER TABLE notification_logs DROP CONSTRAINT IF EXISTS notification_logs_type_check"
    )


def downgrade() -> None:
    op.create_check_constraint(
        "notification_logs_type_check",
        "notification_logs",
        "type = ANY (ARRAY["
        "'price_drop'::text, 'streak_reminder'::text, 'weekly_recap'::text, "
        "'challenge_available'::text, 'cashback_credited'::text, 'level_up'::text"
        "])",
    )
