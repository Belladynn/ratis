"""Rename ocr_knowledge type 'store_header' → 'retailer_header' (DA-34 follow-up of DA-33).

Revision ID: 20260422_0925_retailer_header
Revises: 20260421_2241_store_retailer
Create Date: 2026-04-22 09:25:00.000000+00:00

DA-33 renamed ``stores.brand`` → ``stores.retailer`` but did not touch the
``ocr_knowledge`` CHECK constraint. This migration aligns the OCR knowledge
taxonomy with the retailer terminology :

- UPDATE rows ``type='store_header'`` → ``'retailer_header'``
- Rewrite CHECK ``ck_ocr_knowledge_type`` with the new allowed set.
"""
from __future__ import annotations

from alembic import op

revision = "20260422_0925_retailer_header"
down_revision = "20260421_2241_store_retailer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "DROP CONSTRAINT IF EXISTS ck_ocr_knowledge_type"
    )
    op.execute(
        "UPDATE ocr_knowledge SET type = 'retailer_header' "
        "WHERE type = 'store_header'"
    )
    op.execute(
        "ALTER TABLE ocr_knowledge ADD CONSTRAINT ck_ocr_knowledge_type "
        "CHECK (type IN ('product_name', 'brand_name', 'retailer_header', 'address_token'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ocr_knowledge "
        "DROP CONSTRAINT IF EXISTS ck_ocr_knowledge_type"
    )
    op.execute(
        "UPDATE ocr_knowledge SET type = 'store_header' "
        "WHERE type = 'retailer_header'"
    )
    op.execute(
        "ALTER TABLE ocr_knowledge ADD CONSTRAINT ck_ocr_knowledge_type "
        "CHECK (type IN ('product_name', 'brand_name', 'store_header', 'address_token'))"
    )
