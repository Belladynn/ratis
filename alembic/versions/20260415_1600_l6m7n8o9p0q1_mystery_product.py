"""mystery product tables

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-04-15 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # mystery_challenges
    # ------------------------------------------------------------------
    op.create_table(
        "mystery_challenges",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("product_ean", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "reward_tiers",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["product_ean"], ["products.ean"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('scheduled','active','frozen','revealed')",
            name="mystery_challenges_status_check",
        ),
    )
    # Partial unique index: only one active challenge at a time
    op.create_index(
        "uq_mystery_challenges_active",
        "mystery_challenges",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # ------------------------------------------------------------------
    # mystery_challenge_clues
    # ------------------------------------------------------------------
    op.create_table(
        "mystery_challenge_clues",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("challenge_id", sa.UUID(), nullable=False),
        sa.Column("reveal_day", sa.Integer(), nullable=False),
        sa.Column("clue_text", sa.Text(), nullable=False),
        sa.Column("revealed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"], ["mystery_challenges.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_id", "reveal_day"),
        sa.CheckConstraint(
            "reveal_day BETWEEN 1 AND 3",
            name="mystery_challenge_clues_reveal_day_check",
        ),
    )

    # ------------------------------------------------------------------
    # mystery_challenge_finds
    # ------------------------------------------------------------------
    op.create_table(
        "mystery_challenge_finds",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("challenge_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("cab_awarded", sa.Integer(), nullable=False),
        sa.Column("found_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("announced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["challenge_id"], ["mystery_challenges.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_id", "user_id"),
    )
    op.create_index(
        "ix_mystery_challenge_finds_challenge_id",
        "mystery_challenge_finds",
        ["challenge_id"],
    )

    # ------------------------------------------------------------------
    # mystery_challenge_exclusions
    # ------------------------------------------------------------------
    op.create_table(
        "mystery_challenge_exclusions",
        sa.Column("product_ean", sa.Text(), nullable=False),
        sa.Column(
            "excluded_until", sa.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["product_ean"], ["products.ean"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("product_ean"),
    )


def downgrade() -> None:
    op.drop_table("mystery_challenge_exclusions")
    op.drop_index(
        "ix_mystery_challenge_finds_challenge_id",
        table_name="mystery_challenge_finds",
    )
    op.drop_table("mystery_challenge_finds")
    op.drop_table("mystery_challenge_clues")
    op.drop_index(
        "uq_mystery_challenges_active",
        table_name="mystery_challenges",
    )
    op.drop_table("mystery_challenges")
