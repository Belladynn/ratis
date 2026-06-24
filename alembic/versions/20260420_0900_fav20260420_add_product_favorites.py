"""Add product_favorites table.

Revision ID: fav20260420001
Revises: e7f8a9b0c1d2
Create Date: 2026-04-20 09:00:00.000000+00:00

User-managed pins on a Product (one row per (user_id, product_ean)).
- PK composite (user_id, product_ean) — natural key, prevents duplicates
- FK user_id CASCADE — favorites removed if the user row is hard-deleted
- FK product_ean RESTRICT — EAN is an immutable natural key of products
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fav20260420001"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_favorites",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_ean",
            sa.Text(),
            sa.ForeignKey("products.ean", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "product_ean", name="pk_product_favorites"),
    )
    # PK index on (user_id, product_ean) already covers lookups by user_id prefix,
    # but an explicit index makes the listing query plan unambiguous and decouples
    # the PK column order from the hot query path.
    op.create_index(
        "ix_product_favorites_user_id",
        "product_favorites",
        ["user_id"],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_product_favorites_user_id")
    op.execute("DROP TABLE IF EXISTS product_favorites")
