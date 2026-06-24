"""Referral — extend gift_card_orders with eligible_at + 'referral_reward' source_type.

Revision ID: 20260422_1800_ref_giftcard_elig
Revises: 20260422_1100_pg_trgm_aliases
Create Date: 2026-04-22 18:00:00.000000+00:00

Adds support for the referral gift-card flow:

1. ``gift_card_orders.eligible_at`` — nullable timestamp. When non-null, the
   batch payout will only issue the gift card *after* this moment
   (anti-churn: 30 days delay for referral rewards, immediate for other
   source types which keep ``eligible_at = NULL``).

2. CHECK constraint on ``source_type`` widened to allow the new value
   ``'referral_reward'``. Existing values are preserved.

Zero-downtime migration — all changes are additive or constraint-widening,
no data rewrite.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260422_1800_ref_giftcard_elig"
down_revision = "20260422_1100_pg_trgm_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New nullable column for anti-churn delay (NULL = eligible immediately)
    op.add_column(
        "gift_card_orders",
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Widen source_type CHECK to allow 'referral_reward'. We drop defensively
    #    with IF EXISTS (CLAUDE.md convention for constraint drops).
    op.execute(
        "ALTER TABLE gift_card_orders "
        "DROP CONSTRAINT IF EXISTS ck_gift_card_orders_source_type"
    )
    op.create_check_constraint(
        "ck_gift_card_orders_source_type",
        "gift_card_orders",
        "source_type IN ('annual_subscription', 'battlepass_milestone', "
        "'shop_purchase', 'referral_reward')",
    )


def downgrade() -> None:
    # Downgrade is risky if referral_reward rows already exist (they'd break
    # the narrower CHECK). Best-effort: drop the widened CHECK, then add back
    # the original. A real rollback should purge referral rows first.
    op.execute(
        "ALTER TABLE gift_card_orders "
        "DROP CONSTRAINT IF EXISTS ck_gift_card_orders_source_type"
    )
    op.create_check_constraint(
        "ck_gift_card_orders_source_type",
        "gift_card_orders",
        "source_type IN ('annual_subscription', 'battlepass_milestone', 'shop_purchase')",
    )

    op.drop_column("gift_card_orders", "eligible_at")
