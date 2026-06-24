"""ratis_rewards — CAB, battlepass, missions tables

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-10 10:00:00.000000

Changes:
- user_cab_balance FK CASCADE → RESTRICT
- cabecoin_transactions: complete restructure (direction/amount/reason, SET NULL user_id)
- New: battlepass_seasons, battlepass_milestones, user_battlepass_progress, user_battlepass_claims
- New: missions, user_missions
- cabecoin_transactions: add reference_id UUID + reference_type TEXT + CHECK constraints
- Unique partial index: battlepass_seasons WHERE is_active = TRUE
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # user_cab_balance — change FK CASCADE → RESTRICT
    # ------------------------------------------------------------------
    op.drop_constraint("fk_user", "user_cab_balance", type_="foreignkey")
    op.create_foreign_key(
        "fk_user",
        "user_cab_balance",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ------------------------------------------------------------------
    # cabecoin_transactions — full restructure
    # Drop views that depend on old columns before altering the table
    # ------------------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS leaderboard_weekly")

    # Drop all old constraints then columns, add new columns + constraints.
    # Names differ between fresh DB (from 0001: short names) and existing dev DBs
    # (prefixed names) — use IF EXISTS for both to stay idempotent.
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS action_type_check")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS cabecoin_transactions_action_type_check")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS base_amount_pos")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS cabecoin_transactions_base_amount_check")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS direction_check")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS cabecoin_transactions_direction_check")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS rate_coherence")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS rate_only_video")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS rate_pos")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS boost_is_debit")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS fk_user")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS fk_scan")
    op.execute("ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS fk_receipt")

    op.drop_column("cabecoin_transactions", "action_type")
    op.drop_column("cabecoin_transactions", "base_amount")
    op.drop_column("cabecoin_transactions", "rate")
    op.drop_column("cabecoin_transactions", "rate_reason")
    op.drop_column("cabecoin_transactions", "scan_id")
    op.drop_column("cabecoin_transactions", "receipt_id")

    # Make user_id nullable (SET NULL on delete)
    op.alter_column("cabecoin_transactions", "user_id", nullable=True)

    # Add new columns
    op.add_column("cabecoin_transactions", sa.Column("amount", sa.Integer, nullable=False, server_default="0"))
    op.add_column("cabecoin_transactions", sa.Column("reason", sa.Text, nullable=False, server_default="receipt_scan"))

    # Remove temporary server defaults
    op.alter_column("cabecoin_transactions", "amount", server_default=None)
    op.alter_column("cabecoin_transactions", "reason", server_default=None)

    # Re-create FK with SET NULL
    op.create_foreign_key(
        "cabecoin_transactions_user_id_fkey",
        "cabecoin_transactions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Purge any zero-amount rows backfilled by server_default before applying CHECK
    op.execute("DELETE FROM cabecoin_transactions WHERE amount = 0")

    # New check constraints
    op.create_check_constraint(
        "cabecoin_transactions_direction_check",
        "cabecoin_transactions",
        "direction IN ('credit', 'debit')",
    )
    op.create_check_constraint(
        "cabecoin_transactions_amount_check",
        "cabecoin_transactions",
        "amount > 0",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'mission_reward', 'battlepass_milestone', 'referral', "
        "'cashback_unlock', 'shop_purchase')",
    )

    # Add reference columns for traceability
    op.add_column(
        "cabecoin_transactions",
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "cabecoin_transactions",
        sa.Column("reference_type", sa.Text, nullable=True),
    )
    op.create_check_constraint(
        "cabecoin_transactions_reference_consistency_check",
        "cabecoin_transactions",
        "(reference_id IS NULL) = (reference_type IS NULL)",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reference_type_check",
        "cabecoin_transactions",
        "reference_type IS NULL OR reference_type IN "
        "('scan', 'mission', 'battlepass_milestone', 'referral')",
    )

    # Recreate leaderboard_weekly with new schema (amount replaces base_amount*rate)
    op.execute("""
        CREATE VIEW leaderboard_weekly AS
        SELECT user_id,
            sum(amount) AS cab_earned_week,
            rank() OVER (ORDER BY sum(amount) DESC) AS rank
        FROM cabecoin_transactions
        WHERE direction = 'credit'
          AND created_at >= (now() - '7 days'::interval)
        GROUP BY user_id
    """)

    # ------------------------------------------------------------------
    # BATTLEPASS_SEASONS
    # ------------------------------------------------------------------
    op.create_table(
        "battlepass_seasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("season_number", sa.Integer, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
    )

    # Unique partial index: only one active season at a time
    op.execute(
        "CREATE UNIQUE INDEX uq_one_active_season ON battlepass_seasons (is_active) WHERE is_active = TRUE"
    )

    # ------------------------------------------------------------------
    # BATTLEPASS_MILESTONES
    # ------------------------------------------------------------------
    op.create_table(
        "battlepass_milestones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("season_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("milestone_number", sa.Integer, nullable=False),
        sa.Column("cab_required", sa.Integer, nullable=False),
        sa.Column("reward_type", sa.Text, nullable=False),
        sa.Column("reward_value", sa.Integer, nullable=False),
        sa.Column("subscriber_only", sa.Boolean, nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["season_id"], ["battlepass_seasons.id"], name="fk_season", ondelete="RESTRICT"),
        sa.UniqueConstraint("season_id", "milestone_number", name="uq_season_milestone"),
        sa.CheckConstraint(
            "reward_type IN ('cab', 'gift_card', 'skin')",
            name="battlepass_milestones_reward_type_check",
        ),
    )

    # ------------------------------------------------------------------
    # USER_BATTLEPASS_PROGRESS
    # ------------------------------------------------------------------
    op.create_table(
        "user_battlepass_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("season_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cab_earned_season", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["season_id"], ["battlepass_seasons.id"], name="fk_season", ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "season_id", name="uq_user_season"),
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_set_ubp_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_ubp_updated_at
        BEFORE UPDATE ON user_battlepass_progress
        FOR EACH ROW EXECUTE FUNCTION fn_set_ubp_updated_at()
    """)

    # ------------------------------------------------------------------
    # USER_BATTLEPASS_CLAIMS
    # ------------------------------------------------------------------
    op.create_table(
        "user_battlepass_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("milestone_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["milestone_id"], ["battlepass_milestones.id"], name="fk_milestone", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("user_id", "milestone_id", name="uq_user_milestone"),
    )

    # ------------------------------------------------------------------
    # MISSIONS
    # ------------------------------------------------------------------
    op.create_table(
        "missions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("frequency", sa.Text, nullable=False),
        sa.Column("difficulty", sa.Text, nullable=False),
        sa.Column("target_count", sa.Integer, nullable=False),
        sa.Column("cab_reward", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("action_type", "frequency", "difficulty", name="uq_mission"),
        sa.CheckConstraint(
            "action_type IN ('receipt_scan', 'label_scan', 'barcode_scan', 'price_compared')",
            name="missions_action_type_check",
        ),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly')",
            name="missions_frequency_check",
        ),
        sa.CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="missions_difficulty_check",
        ),
    )

    # ------------------------------------------------------------------
    # USER_MISSIONS
    # ------------------------------------------------------------------
    op.create_table(
        "user_missions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("current_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], name="fk_mission", ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "mission_id", "period_start", name="uq_user_mission_period"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'claimed')",
            name="user_missions_status_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_missions")
    op.drop_table("missions")
    op.drop_table("user_battlepass_claims")
    op.execute("DROP TRIGGER IF EXISTS trg_ubp_updated_at ON user_battlepass_progress")
    op.execute("DROP FUNCTION IF EXISTS fn_set_ubp_updated_at")
    op.drop_table("user_battlepass_progress")
    op.drop_table("battlepass_milestones")
    op.execute("DROP INDEX IF EXISTS uq_one_active_season")
    op.drop_table("battlepass_seasons")

    # Restore cabecoin_transactions to original schema
    op.execute("DROP VIEW IF EXISTS leaderboard_weekly")
    op.drop_constraint(
        "cabecoin_transactions_reference_type_check", "cabecoin_transactions", type_="check"
    )
    op.drop_constraint(
        "cabecoin_transactions_reference_consistency_check", "cabecoin_transactions", type_="check"
    )
    op.drop_column("cabecoin_transactions", "reference_type")
    op.drop_column("cabecoin_transactions", "reference_id")
    op.drop_constraint("cabecoin_transactions_reason_check", "cabecoin_transactions", type_="check")
    op.drop_constraint("cabecoin_transactions_amount_check", "cabecoin_transactions", type_="check")
    op.drop_constraint("cabecoin_transactions_direction_check", "cabecoin_transactions", type_="check")
    op.drop_constraint("cabecoin_transactions_user_id_fkey", "cabecoin_transactions", type_="foreignkey")

    op.drop_column("cabecoin_transactions", "reason")
    op.drop_column("cabecoin_transactions", "amount")
    op.alter_column("cabecoin_transactions", "user_id", nullable=False)

    op.add_column(
        "cabecoin_transactions",
        sa.Column("action_type", sa.Text, nullable=False, server_default="SCAN_RECEIPT"),
    )
    op.add_column("cabecoin_transactions", sa.Column("base_amount", sa.Integer, nullable=False, server_default="0"))
    op.add_column("cabecoin_transactions", sa.Column("rate", sa.Numeric(4, 2), nullable=True))
    op.add_column("cabecoin_transactions", sa.Column("rate_reason", sa.Text, nullable=True))
    op.add_column("cabecoin_transactions", sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("cabecoin_transactions", sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.create_foreign_key("fk_user", "cabecoin_transactions", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_scan", "cabecoin_transactions", "scans", ["scan_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(
        "fk_receipt", "cabecoin_transactions", "receipts", ["receipt_id"], ["id"], ondelete="SET NULL"
    )

    op.alter_column("cabecoin_transactions", "action_type", server_default=None)
    op.alter_column("cabecoin_transactions", "base_amount", server_default=None)

    # Restore leaderboard_weekly with old schema
    op.execute("""
        CREATE VIEW leaderboard_weekly AS
        SELECT user_id,
            sum(base_amount::numeric * COALESCE(rate, 1::numeric)) AS cab_earned_week,
            rank() OVER (ORDER BY sum(base_amount::numeric * COALESCE(rate, 1::numeric)) DESC) AS rank
        FROM cabecoin_transactions
        WHERE direction = 'credit'
          AND created_at >= (now() - '7 days'::interval)
        GROUP BY user_id
    """)

    # Restore user_cab_balance FK CASCADE
    op.drop_constraint("fk_user", "user_cab_balance", type_="foreignkey")
    op.create_foreign_key("fk_user", "user_cab_balance", "users", ["user_id"], ["id"], ondelete="CASCADE")
