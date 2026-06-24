"""rework_product_knowledge

Revision ID: a1b2c3d4e5f6
Revises: e8f2a1b3c4d5
Create Date: 2026-04-08 09:00:00.000000+00:00
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e8f2a1b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old product_knowledge (had product_ean FK, corrected/confidence NOT NULL, no match_type)
    op.drop_table("product_knowledge")

    # Recreate with new schema
    op.create_table(
        "product_knowledge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_ocr", sa.Text(), nullable=False),
        sa.Column("corrected", sa.Text(), nullable=True),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("raw_ocr", name="uq_product_knowledge_raw_ocr"),
        sa.CheckConstraint(
            "match_type IN ('sequence', 'ngram', 'token')",
            name="ck_product_knowledge_match_type",
        ),
        sa.CheckConstraint(
            "source IN ('ocr_arbitrage', 'user_correction', 'manual')",
            name="ck_product_knowledge_source",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_product_knowledge_confidence",
        ),
    )

    # Remove 'knowledge' from scans.match_method (product_knowledge no longer resolves EANs)
    op.drop_constraint("ck_scans_match_method", "scans")
    op.create_check_constraint(
        "ck_scans_match_method",
        "scans",
        "match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual')",
    )


def downgrade() -> None:
    op.drop_table("product_knowledge")
    op.drop_constraint("ck_scans_match_method", "scans")
    op.create_check_constraint(
        "ck_scans_match_method",
        "scans",
        "match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'knowledge')",
    )
    # Restore old product_knowledge with product_ean
    op.create_table(
        "product_knowledge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
