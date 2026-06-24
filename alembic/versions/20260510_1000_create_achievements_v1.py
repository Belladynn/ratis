"""create_achievements_v1

Revision ID: 20260510_1000_ach_v1
Revises: 20260509_1200_bbv1
Create Date: 2026-05-10 10:00:00.000000

Achievements V1 — schema foundation.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Data model.

Creates:
  - 3 ENUMs : achievement_rarity, achievement_category, achievement_trigger_type
  - 2 tables : achievements (catalog), user_achievements (instances)
  - 5 indexes (2 on achievements + 1 partial + 2 on user_achievements)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers (≤32 chars per R08).
revision = "20260510_1000_ach_v1"
down_revision = "20260509_1200_bbv1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 3 ENUMs
    op.execute(
        """
        CREATE TYPE achievement_rarity AS ENUM (
            'terracotta', 'bronze', 'copper', 'silver', 'gold',
            'emerald', 'sapphire', 'ruby', 'crystal', 'diamond'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE achievement_category AS ENUM (
            'volume', 'savings', 'streak', 'social',
            'exploration', 'seasonal', 'secret', 'j_y_etais'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE achievement_trigger_type AS ENUM (
            'scan_count',
            'savings_eur_total',
            'savings_eur_in_window',
            'streak_days',
            'referral_count',
            'unique_brands_count',
            'unique_categories_count',
            'unique_products_discovered_count',
            'first_event'
        )
        """
    )

    # achievements (catalog)
    op.create_table(
        "achievements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=False),
        sa.Column(
            "rarity",
            postgresql.ENUM(name="achievement_rarity", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "category",
            postgresql.ENUM(name="achievement_category", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "trigger_type",
            postgresql.ENUM(name="achievement_trigger_type", create_type=False),
            nullable=False,
        ),
        sa.Column("target_value", sa.Numeric(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("extra_params", postgresql.JSONB(), nullable=True),
        sa.Column("cab_reward", sa.Integer(), nullable=False),
        sa.Column(
            "is_secret",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_hidden",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "display_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "target_value > 0", name="ck_achievements_target_positive"
        ),
        sa.CheckConstraint(
            "cab_reward >= 0", name="ck_achievements_cab_nonneg"
        ),
        sa.CheckConstraint(
            "window_days IS NULL OR window_days > 0",
            name="ck_achievements_window_positive",
        ),
        sa.CheckConstraint(
            "available_until IS NULL OR available_from IS NULL OR "
            "available_until > available_from",
            name="ck_achievements_window_consistent",
        ),
        sa.CheckConstraint(
            "category != 'j_y_etais'",
            name="ck_achievements_no_jyetais_in_catalog",
        ),
    )
    op.create_index(
        "idx_achievements_trigger_type", "achievements", ["trigger_type"]
    )
    op.create_index(
        "idx_achievements_category", "achievements", ["category"]
    )
    # Partial index — only rows with a window defined.
    op.execute(
        """
        CREATE INDEX idx_achievements_window
        ON achievements (available_from, available_until)
        WHERE available_from IS NOT NULL OR available_until IS NOT NULL
        """
    )

    # user_achievements (instances)
    op.create_table(
        "user_achievements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "achievement_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("cab_granted", sa.Integer(), nullable=False),
        sa.Column("trigger_event", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["achievement_id"], ["achievements.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "user_id", "achievement_id", name="uq_user_achievements_pair"
        ),
    )
    op.create_index(
        "idx_user_achievements_user",
        "user_achievements",
        ["user_id", sa.text("unlocked_at DESC")],
    )
    op.create_index(
        "idx_user_achievements_achievement",
        "user_achievements",
        ["achievement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_user_achievements_achievement", table_name="user_achievements"
    )
    op.drop_index(
        "idx_user_achievements_user", table_name="user_achievements"
    )
    op.drop_table("user_achievements")
    op.execute("DROP INDEX IF EXISTS idx_achievements_window")
    op.drop_index("idx_achievements_category", table_name="achievements")
    op.drop_index("idx_achievements_trigger_type", table_name="achievements")
    op.drop_table("achievements")
    op.execute("DROP TYPE IF EXISTS achievement_trigger_type")
    op.execute("DROP TYPE IF EXISTS achievement_category")
    op.execute("DROP TYPE IF EXISTS achievement_rarity")
