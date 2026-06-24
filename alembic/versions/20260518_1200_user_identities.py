"""Phase 2 — create user_identities table + backfill from users.(provider, provider_id).

Revision ID: 20260518_1200_user_identities
Revises: 20260518_1130_merge_heads_2
Create Date: 2026-05-18 12:00:00.000000

Each non-NULL ``users.(provider, provider_id)`` row produces exactly one
``user_identities`` row. The backfill aborts if it would create a
duplicate ``(provider, provider_id)`` — the UNIQUE constraint is added
AFTER the backfill so a clean error fires here, not a half-applied table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260518_1200_user_identities"
down_revision = "20260518_1130_merge_heads_2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("provider IN ('google', 'apple')", name="user_identities_provider_check"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"], unique=False)

    # --- Duplicate pre-check : abort before adding UNIQUE if the source
    # data already carries a colliding (provider, provider_id) pair. ---
    conn = op.get_bind()
    dups = conn.execute(
        sa.text(
            "SELECT provider, provider_id, COUNT(*) AS n FROM users "
            "WHERE provider IN ('google', 'apple') AND provider_id IS NOT NULL "
            "GROUP BY provider, provider_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dups:
        raise RuntimeError(
            f"Cannot backfill user_identities — duplicate (provider, provider_id) "
            f"pairs in users: {[(d.provider, d.provider_id, d.n) for d in dups]}"
        )

    # --- Backfill : one identity row per OAuth users row. ---
    conn.execute(
        sa.text(
            "INSERT INTO user_identities (id, user_id, provider, provider_id, email, created_at) "
            "SELECT gen_random_uuid(), id, provider, provider_id, email, created_at "
            "FROM users "
            "WHERE provider IN ('google', 'apple') AND provider_id IS NOT NULL"
        )
    )

    # --- UNIQUE constraint added last (post-backfill, post-pre-check). ---
    op.create_unique_constraint(
        "user_identities_provider_provider_id_key",
        "user_identities",
        ["provider", "provider_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
