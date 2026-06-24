"""Community challenges — 5 new tables + challenge_milestone reason

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-04-14 16:00:00

Changes:
  1. community_challenges          — new table
  2. community_challenge_milestones — new table
  3. community_challenge_progress   — new table
  4. community_challenge_claims     — new table
  5. community_multipliers          — new table
  6. cabecoin_transactions          — add 'challenge_milestone' to reason CHECK
                                     add 'community_challenge_milestone' to reference_type CHECK
  7. xp_transactions                — add 'challenge_milestone' to reason CHECK
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "i2j3k4l5m6n7"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. community_challenges
    # ------------------------------------------------------------------
    op.create_table(
        "community_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("action_filter", postgresql.JSONB, nullable=True),
        sa.Column("objective", sa.Integer, nullable=False),
        sa.Column(
            "starts_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grace_period_days", sa.Integer, nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # One active challenge at a time
    op.create_index(
        "community_challenges_one_active",
        "community_challenges",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )

    # ------------------------------------------------------------------
    # 2. community_challenge_milestones
    # ------------------------------------------------------------------
    op.create_table(
        "community_challenge_milestones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_challenges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("threshold", sa.Integer, nullable=False),
        sa.Column("reward_type", sa.Text, nullable=False),
        sa.Column("reward_value", postgresql.JSONB, nullable=False),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "reward_type IN ('cab', 'xp', 'skin', 'multiplier')",
            name="community_challenge_milestones_reward_type_check",
        ),
    )

    # ------------------------------------------------------------------
    # 3. community_challenge_progress  (one row per challenge)
    # ------------------------------------------------------------------
    op.create_table(
        "community_challenge_progress",
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_challenges.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "current_count >= 0",
            name="community_challenge_progress_count_nn",
        ),
    )

    # ------------------------------------------------------------------
    # 4. community_challenge_claims
    # ------------------------------------------------------------------
    op.create_table(
        "community_challenge_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_challenges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "milestone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_challenge_milestones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("milestone_id", "user_id", name="uq_challenge_claims_milestone_user"),
    )

    # ------------------------------------------------------------------
    # 5. community_multipliers
    # ------------------------------------------------------------------
    op.create_table(
        "community_multipliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("community_challenges.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("multiplier", sa.Numeric, nullable=False),
        sa.Column("applies_to", sa.Text, nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "challenge_id", "user_id", name="uq_community_multipliers_challenge_user"
        ),
        sa.CheckConstraint(
            "applies_to IN ('cab', 'xp', 'both')",
            name="community_multipliers_applies_to_check",
        ),
    )

    # ------------------------------------------------------------------
    # 6. cabecoin_transactions — add 'challenge_milestone' to reason CHECK
    #                          — add 'community_challenge_milestone' to reference_type CHECK
    # ------------------------------------------------------------------
    op.drop_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'mission_reward', 'battlepass_milestone', 'referral', "
        "'cashback_boost_debit', 'cashback_boost_refund', 'shop_purchase', "
        "'stonks_boost', 'mission_freeze', "
        "'food_reserve_purchase', 'streak_repair', "
        "'challenge_milestone'"
        ")",
    )
    op.drop_constraint(
        "cabecoin_transactions_reference_type_check",
        "cabecoin_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reference_type_check",
        "cabecoin_transactions",
        "reference_type IS NULL OR reference_type IN ("
        "'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
        "'community_challenge_milestone'"
        ")",
    )

    # ------------------------------------------------------------------
    # 7. xp_transactions — add 'challenge_milestone' to reason CHECK
    # ------------------------------------------------------------------
    op.drop_constraint("xp_reason_check", "xp_transactions", type_="check")
    op.create_check_constraint(
        "xp_reason_check",
        "xp_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', 'price_compared', "
        "'mission_completed', 'battlepass_milestone', 'referral', "
        "'feed_jack', 'stonks_completion', "
        "'challenge_milestone'"
        ")",
    )


def downgrade() -> None:
    # Restore xp_transactions reason check (without challenge_milestone)
    op.drop_constraint("xp_reason_check", "xp_transactions", type_="check")
    op.create_check_constraint(
        "xp_reason_check",
        "xp_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', 'price_compared', "
        "'mission_completed', 'battlepass_milestone', 'referral', "
        "'feed_jack', 'stonks_completion'"
        ")",
    )

    # Restore cabecoin_transactions constraints
    op.drop_constraint(
        "cabecoin_transactions_reference_type_check",
        "cabecoin_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reference_type_check",
        "cabecoin_transactions",
        "reference_type IS NULL OR reference_type IN ("
        "'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission'"
        ")",
    )
    op.drop_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'mission_reward', 'battlepass_milestone', 'referral', "
        "'cashback_boost_debit', 'cashback_boost_refund', 'shop_purchase', "
        "'stonks_boost', 'mission_freeze', "
        "'food_reserve_purchase', 'streak_repair'"
        ")",
    )

    # Drop tables in reverse order
    op.drop_table("community_multipliers")
    op.drop_table("community_challenge_claims")
    op.drop_table("community_challenge_progress")
    op.drop_table("community_challenge_milestones")
    op.drop_index("community_challenges_one_active", table_name="community_challenges")
    op.drop_table("community_challenges")
