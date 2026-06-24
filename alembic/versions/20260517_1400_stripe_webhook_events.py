"""Add stripe_webhook_events dedup table (audit C2).

Revision ID: 20260517_1400_stripe_evt
Revises: 20260517_1300_namenorm_nn
Create Date: 2026-05-17 14:00:00.000000+00:00

Audit finding C2 — ``stripe_webhook`` had no idempotency guard: Stripe
retries a webhook on timeout / 5xx, causing the same event to arrive
multiple times. ``_maybe_trigger_annual_gift_card`` had no guard, so each
retry re-enqueued ``trigger_annual_gift_card`` → duplicate annual gift-card
issuance (real money).

Fix: introduce ``stripe_webhook_events`` as an idempotency ledger. The
route claims every ``event_id`` (INSERT ... ON CONFLICT DO NOTHING) at
the HEAD of the handler; a duplicate arrival finds the row already present
(rowcount == 0) and short-circuits before any side effects.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260517_1400_stripe_evt"
down_revision = "20260517_1300_namenorm_nn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),
    )


def downgrade() -> None:
    op.drop_table("stripe_webhook_events")
