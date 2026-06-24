"""Add user_savings_snapshot table — hybrid snapshot for total savings + ROI rings.

Revision ID: 20260421_2000_savings_snap
Revises: fav20260420001
Create Date: 2026-04-21 10:00:00.000000+00:00

Each user gets one row :
- `lifetime_savings_cents` — recomputed nightly by ratis_batch_savings.
- `rings_consumed` — atomic counter incremented by POST /account/rings/claim.
- `last_computed_at` — watermark used by /account/stats to live-recompute the
  delta since the last batch (fire-and-forget snapshot, hot path stays cheap).

`rings_consumed` is BIGINT : infinite prestige progression, the frontend
derives visual tier from the raw value — backend stays visual-agnostic.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260421_2000_savings_snap"
down_revision = "20260421_1800_unknown_aggregate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_savings_snapshot",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "lifetime_savings_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rings_consumed",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_computed_at",
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
            "lifetime_savings_cents >= 0",
            name="ck_user_savings_snapshot_lifetime_nonneg",
        ),
        sa.CheckConstraint(
            "rings_consumed >= 0",
            name="ck_user_savings_snapshot_rings_nonneg",
        ),
    )

    # Updated_at trigger — CLAUDE.md rule : PostgreSQL triggers only, never
    # SQLAlchemy `onupdate`. Re-uses the global fn_set_updated_at() function
    # declared in the initial schema.
    op.execute("""
        CREATE OR REPLACE TRIGGER trg_user_savings_snapshot_updated_at
        BEFORE UPDATE ON user_savings_snapshot
        FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_savings_snapshot_updated_at "
        "ON user_savings_snapshot"
    )
    op.execute("DROP TABLE IF EXISTS user_savings_snapshot")
