"""Buffer + Burst V1 — refonte Stonks

Revision ID: 20260509_1200_bbv1
Revises: 20260509_0100_disqual
Create Date: 2026-05-09 12:00:00

Brainstorm 2026-05-09 a remplacé l'ancienne mécanique Stonks par deux
mécaniques distinctes :

- **Buffer** (= ex-Stonks renommé) : extension de fenêtre + augmentation
  objectif/récompense. Multi-claim cumulatif via double gating.
- **Burst** : déblocage passif de paliers XP exponentiels après
  dépassement de l'objectif. 0 CAB additionnel.

Cette migration applique le delta DB minimal :

1. Rename `user_missions.boost_count` → `buffer_count`
2. Add 4 cols `user_missions` : `burst_count`, `period_extended_until`,
   `burst_locked`, `portions_claimed`
3. Drop table `stonks_records` (= remplacée par `mission_xp_records`)
4. Create table `mission_xp_records` (= leaderboard XP par mission)

Réversible. Les `cabecoin_transactions.reason` ENUM values
`stonks_boost` / `stonks_completion` (xp_transactions) restent acceptés
côté CHECK constraint pour préserver l'audit trail historique pré-migration
(décision spec 2026-05-09 : pas de touchage des reasons, on réutilise
`mission_reward` pour les claims Buffer).

Référence design doc :
``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260509_1200_bbv1"
down_revision = "20260509_0100_disqual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --------------------------------------------------------------- #
    # user_missions — rename boost_count → buffer_count                #
    # --------------------------------------------------------------- #
    op.alter_column(
        "user_missions",
        "boost_count",
        new_column_name="buffer_count",
    )

    # --------------------------------------------------------------- #
    # user_missions — add Buffer + Burst columns                       #
    # --------------------------------------------------------------- #
    op.add_column(
        "user_missions",
        sa.Column(
            "burst_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "user_missions",
        sa.Column(
            "period_extended_until",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "user_missions",
        sa.Column(
            "burst_locked",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "user_missions",
        sa.Column(
            "portions_claimed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # --------------------------------------------------------------- #
    # Drop obsolete table stonks_records                               #
    # --------------------------------------------------------------- #
    op.execute(
        "DROP INDEX IF EXISTS ix_stonks_records_user_id"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_stonks_records_period_xp"
    )
    op.execute("DROP TABLE IF EXISTS stonks_records")

    # --------------------------------------------------------------- #
    # mission_xp_records — Burst leaderboard table                     #
    # --------------------------------------------------------------- #
    op.create_table(
        "mission_xp_records",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=False),
        sa.Column("user_mission_id", sa.UUID(), nullable=False),
        sa.Column("xp_earned", sa.Numeric(), nullable=False),
        sa.Column("burst_count", sa.Integer(), nullable=False),
        sa.Column(
            "buffer_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_mission_id"],
            ["user_missions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_mission_id", name="uq_mxr_user_mission"
        ),
        sa.CheckConstraint(
            "xp_earned > 0", name="mxr_xp_earned_positive"
        ),
        sa.CheckConstraint(
            "burst_count >= 0", name="mxr_burst_count_nn"
        ),
        sa.CheckConstraint(
            "buffer_count >= 0", name="mxr_buffer_count_nn"
        ),
    )
    # Index expression uses ``date_trunc('month', recorded_at AT TIME
    # ZONE 'UTC')`` so the result is IMMUTABLE (vanilla
    # ``date_trunc(text, timestamptz)`` is STABLE and rejected for
    # index expressions).
    op.create_index(
        "ix_mxr_user_month",
        "mission_xp_records",
        [
            "user_id",
            sa.text("date_trunc('month', recorded_at AT TIME ZONE 'UTC')"),
        ],
    )
    op.create_index(
        "ix_mxr_xp_alltime",
        "mission_xp_records",
        [sa.text("xp_earned DESC")],
    )

    # --------------------------------------------------------------- #
    # xp_transactions — extend xp_reason_check with 'mission_burst'    #
    # --------------------------------------------------------------- #
    # Burst paliers award XP via reason='mission_burst'. Legacy
    # 'stonks_completion' stays accepted for historical rows (the
    # runtime no longer emits it after this migration).
    op.execute(
        "ALTER TABLE xp_transactions DROP CONSTRAINT IF EXISTS "
        "xp_reason_check"
    )
    _xp_reasons = (
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'product_identification', 'fill_product_field', 'scan_distinct', "
        "'promo_found', 'price_compared', 'mission_completed', "
        "'battlepass_milestone', 'referral', 'feed_jack', "
        "'stonks_completion', 'challenge_milestone', 'mission_burst'"
    )
    op.create_check_constraint(
        "xp_reason_check",
        "xp_transactions",
        f"reason IN ({_xp_reasons})",
    )


def downgrade() -> None:
    # --------------------------------------------------------------- #
    # xp_transactions — restore xp_reason_check without 'mission_burst' #
    # --------------------------------------------------------------- #
    op.execute(
        "ALTER TABLE xp_transactions DROP CONSTRAINT IF EXISTS "
        "xp_reason_check"
    )
    _xp_legacy = (
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'product_identification', 'fill_product_field', 'scan_distinct', "
        "'promo_found', 'price_compared', 'mission_completed', "
        "'battlepass_milestone', 'referral', 'feed_jack', "
        "'stonks_completion', 'challenge_milestone'"
    )
    op.create_check_constraint(
        "xp_reason_check",
        "xp_transactions",
        f"reason IN ({_xp_legacy})",
    )

    # --------------------------------------------------------------- #
    # Drop mission_xp_records                                          #
    # --------------------------------------------------------------- #
    op.execute("DROP INDEX IF EXISTS ix_mxr_xp_alltime")
    op.execute("DROP INDEX IF EXISTS ix_mxr_user_month")
    op.drop_table("mission_xp_records")

    # --------------------------------------------------------------- #
    # Re-create stonks_records (mirror of original schema)             #
    # --------------------------------------------------------------- #
    op.create_table(
        "stonks_records",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "mission_id",
            "period",
            name="stonks_records_best_per_period",
        ),
    )
    op.create_index(
        "ix_stonks_records_user_id", "stonks_records", ["user_id"]
    )
    op.create_index(
        "ix_stonks_records_period_xp",
        "stonks_records",
        ["period", "xp_earned"],
    )

    # --------------------------------------------------------------- #
    # user_missions — drop Buffer + Burst columns                      #
    # --------------------------------------------------------------- #
    op.drop_column("user_missions", "portions_claimed")
    op.drop_column("user_missions", "burst_locked")
    op.drop_column("user_missions", "period_extended_until")
    op.drop_column("user_missions", "burst_count")

    # --------------------------------------------------------------- #
    # user_missions — rename buffer_count → boost_count                #
    # --------------------------------------------------------------- #
    op.alter_column(
        "user_missions",
        "buffer_count",
        new_column_name="boost_count",
    )
