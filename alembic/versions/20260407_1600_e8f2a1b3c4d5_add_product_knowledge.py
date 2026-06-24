"""add_product_knowledge

Revision ID: e8f2a1b3c4d5
Revises: d5e2f1a3b7c9
Create Date: 2026-04-07 16:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8f2a1b3c4d5"
down_revision: Union[str, None] = "d5e2f1a3b7c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_knowledge",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_ocr", sa.Text(), nullable=False),
        sa.Column("corrected", sa.Text(), nullable=False),
        sa.Column("product_ean", sa.Text(), sa.ForeignKey("products.ean", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("raw_ocr", name="uq_product_knowledge_raw_ocr"),
        sa.CheckConstraint(
            "source IN ('ocr_arbitrage', 'user_correction', 'manual')",
            name="ck_product_knowledge_source",
        ),
    )

    # Extend scans.match_method to include 'knowledge'
    op.drop_constraint("ck_scans_match_method", "scans")
    op.create_check_constraint(
        "ck_scans_match_method",
        "scans",
        "match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'knowledge')",
    )


def downgrade() -> None:
    op.drop_table("product_knowledge")
    op.drop_constraint("ck_scans_match_method", "scans")
    op.create_check_constraint(
        "ck_scans_match_method",
        "scans",
        "match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual')",
    )
