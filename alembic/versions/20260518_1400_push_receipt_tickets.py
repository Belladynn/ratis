"""Create push_receipt_tickets — track Expo push tickets for receipt polling.

Revision ID: 20260518_1400_pushrcpt
Revises: 20260518_1400_receipt_idem
Create Date: 2026-05-18 14:00:00.000000

Expo's push API returns one *ticket* per accepted push ; the final delivery
outcome is only known by later polling Expo's *receipts* endpoint with the
ticket IDs. ratis_notifier previously discarded the tickets, so dead push
tokens (Expo error ``DeviceNotRegistered``) were never cleaned up.

``push_receipt_tickets`` persists one row per (push send, token) so the
``ratis_batch_push_receipts`` batch can fetch receipts and delete dead
tokens. ``push_token`` is the token *string* (not an FK) so the cleanup is
a direct lookup and the ticket row survives the token's deletion — keeping
an audit trail. Rows are purged after 7 days by ``ratis_batch_purge`` (an
Expo receipt is only retained ~24h upstream, so an unchecked row older than
that is dead weight).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260518_1400_pushrcpt"
down_revision = "20260518_1400_receipt_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_receipt_tickets",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("expo_ticket_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("push_token", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("expo_ticket_id", name="uq_push_receipt_tickets_ticket"),
    )
    # Partial index — the batch only ever scans not-yet-checked rows.
    op.create_index(
        "ix_push_receipt_tickets_unchecked",
        "push_receipt_tickets",
        ["created_at"],
        postgresql_where=sa.text("checked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_push_receipt_tickets_unchecked", table_name="push_receipt_tickets"
    )
    op.drop_table("push_receipt_tickets")
