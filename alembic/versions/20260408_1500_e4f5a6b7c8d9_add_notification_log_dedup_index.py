"""add dedup unique index on notification_logs

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-04-08 15:00:00.000000

Partial unique index on (user_id, type, date_trunc('minute', sent_at))
restricted to status = 'sent'. Prevents concurrent requests from creating
duplicate "sent" log entries for the same user + notification type within
the same calendar minute.
"""
from __future__ import annotations

from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX ix_notification_logs_dedup_sent
        ON notification_logs (user_id, type, date_trunc('minute', sent_at AT TIME ZONE 'UTC'))
        WHERE status = 'sent'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notification_logs_dedup_sent")
