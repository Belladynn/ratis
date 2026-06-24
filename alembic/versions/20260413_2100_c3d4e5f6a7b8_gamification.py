"""gamification — XP system, Stonks, photo hash, mission freeze

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-13 21:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # photo_hash — receipts (tickets) + scans (electronic_label only)    #
    # ------------------------------------------------------------------ #
    # receipts: 1 ticket = 1 Receipt + N Scan (1 per product line).
    # The photo lives on receipts.image_r2_key — hash must live here too.
    op.add_column("receipts", sa.Column("photo_hash", sa.CHAR(64), nullable=True))
    op.create_index(
        "receipts_photo_hash_unique",
        "receipts",
        ["photo_hash"],
        unique=True,
        postgresql_where=sa.text("photo_hash IS NOT NULL"),
    )
    # scans: electronic_label photos — one scan per photo.
    # Partial index scoped to scan_type to avoid false conflicts on receipt/barcode rows.
    op.add_column("scans", sa.Column("photo_hash", sa.CHAR(64), nullable=True))
    op.create_index(
        "scans_photo_hash_unique",
        "scans",
        ["photo_hash"],
        unique=True,
        postgresql_where=sa.text(
            "photo_hash IS NOT NULL AND scan_type = 'electronic_label'"
        ),
    )

    # ------------------------------------------------------------------ #
    # user_xp_balance                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "user_xp_balance",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("balance", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.CheckConstraint("balance >= 0", name="user_xp_balance_positive"),
    )

    # ------------------------------------------------------------------ #
    # xp_transactions                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "xp_transactions",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("reference_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "reason IN ("
            "'receipt_scan','label_scan','barcode_scan','price_compared',"
            "'mission_completed','battlepass_milestone','referral',"
            "'feed_jack','stonks_completion'"
            ")",
            name="xp_reason_check",
        ),
        sa.CheckConstraint("amount > 0", name="xp_amount_positive"),
    )
    op.create_index("ix_xp_transactions_user_id", "xp_transactions", ["user_id"])

    # ------------------------------------------------------------------ #
    # missions catalogue — is_boostable                                    #
    # ------------------------------------------------------------------ #
    op.add_column(
        "missions",
        sa.Column("is_boostable", sa.Boolean(), nullable=False, server_default="true"),
    )
    # receipt_scan missions non boostables (philosophie produit)
    op.execute(
        "UPDATE missions SET is_boostable = FALSE WHERE action_type = 'receipt_scan'"
    )

    # ------------------------------------------------------------------ #
    # user_missions — boost + freeze columns                               #
    # ------------------------------------------------------------------ #
    op.add_column("user_missions", sa.Column("boost_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("user_missions", sa.Column("cab_reward", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("user_missions", sa.Column("xp_reward", sa.Numeric(), nullable=False, server_default="0"))
    op.add_column("user_missions", sa.Column("frozen_until", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("user_missions", sa.Column("freeze_count", sa.Integer(), nullable=False, server_default="0"))

    # ------------------------------------------------------------------ #
    # stonks_records                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "stonks_records",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=False),
        sa.Column("boost_count", sa.Integer(), nullable=False),
        sa.Column("xp_earned", sa.Numeric(), nullable=False),
        sa.Column("cab_earned", sa.Integer(), nullable=False),
        sa.Column("period", sa.CHAR(7), nullable=False),
        sa.Column(
            "achieved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stonks_records_user_id", "stonks_records", ["user_id"])
    op.create_index(
        "ix_stonks_records_period_xp",
        "stonks_records",
        ["period", "xp_earned"],
    )
    op.create_unique_constraint(
        "stonks_records_best_per_period",
        "stonks_records",
        ["user_id", "mission_id", "period"],
    )


def downgrade() -> None:
    op.drop_table("stonks_records")
    op.drop_column("user_missions", "freeze_count")
    op.drop_column("user_missions", "frozen_until")
    op.drop_column("user_missions", "xp_reward")
    op.drop_column("user_missions", "cab_reward")
    op.drop_column("user_missions", "boost_count")
    op.drop_column("missions", "is_boostable")
    op.drop_table("xp_transactions")
    op.drop_table("user_xp_balance")
    op.drop_index("scans_photo_hash_unique", table_name="scans")
    op.drop_column("scans", "photo_hash")
    op.drop_index("receipts_photo_hash_unique", table_name="receipts")
    op.drop_column("receipts", "photo_hash")
