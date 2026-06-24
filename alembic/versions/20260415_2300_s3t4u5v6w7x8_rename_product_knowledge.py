"""rename_product_knowledge

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-04-15 23:00:00.000000+00:00

Renomme product_knowledge → ocr_knowledge.
Ajoute colonne type TEXT NOT NULL DEFAULT 'product_name'.
Remplace l'unique constraint (raw_ocr) par (raw_ocr, type).
Renomme les CHECK constraints.
Ajoute CHECK type IN ('product_name', 'brand_name', 'store_header', 'address_token').
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("product_knowledge", "ocr_knowledge")

    # Add type column with server_default so existing rows get 'product_name'
    op.add_column(
        "ocr_knowledge",
        sa.Column("type", sa.Text(), nullable=False, server_default="product_name"),
    )
    # Remove the server_default (we want NULL to fail, but all existing rows are covered)
    op.alter_column("ocr_knowledge", "type", server_default=None)

    # Drop old unique constraint on raw_ocr alone (IF EXISTS: safe on re-apply)
    op.execute("ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS uq_product_knowledge_raw_ocr")
    # New unique on (raw_ocr, type)
    op.create_unique_constraint(
        "uq_ocr_knowledge_raw_ocr_type", "ocr_knowledge", ["raw_ocr", "type"]
    )

    # Rename existing check constraints
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "RENAME CONSTRAINT ck_product_knowledge_match_type TO ck_ocr_knowledge_match_type"
    )
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "RENAME CONSTRAINT ck_product_knowledge_source TO ck_ocr_knowledge_source"
    )
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "RENAME CONSTRAINT ck_product_knowledge_confidence TO ck_ocr_knowledge_confidence"
    )

    # Add type check constraint
    op.create_check_constraint(
        "ck_ocr_knowledge_type",
        "ocr_knowledge",
        "type IN ('product_name', 'brand_name', 'store_header', 'address_token')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ocr_knowledge_type", "ocr_knowledge", type_="check")
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "RENAME CONSTRAINT ck_ocr_knowledge_confidence TO ck_product_knowledge_confidence"
    )
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "RENAME CONSTRAINT ck_ocr_knowledge_source TO ck_product_knowledge_source"
    )
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "RENAME CONSTRAINT ck_ocr_knowledge_match_type TO ck_product_knowledge_match_type"
    )
    op.execute("ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS uq_ocr_knowledge_raw_ocr_type")
    op.create_unique_constraint(
        "uq_product_knowledge_raw_ocr", "ocr_knowledge", ["raw_ocr"]
    )
    op.drop_column("ocr_knowledge", "type")
    op.rename_table("ocr_knowledge", "product_knowledge")
