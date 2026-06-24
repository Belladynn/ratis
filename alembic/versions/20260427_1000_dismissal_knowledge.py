"""ocr_knowledge — extend for dismissal feedback loop (LLM 3-bucket).

Revision ID: 20260427_1000_dismissal
Revises: 20260426_1500_scans_price_nonneg
Create Date: 2026-04-27 10:00:00.000000+00:00

The LLM filter (AF-12 part 2) classifies receipt OCR fragments into 3 buckets :
retailer / products / dismissals. Dismissals are boilerplate (payment methods,
totals, footer slogans) that we want to learn so the next receipt can
pre-filter them locally without re-asking the LLM.

We extend the existing ``ocr_knowledge`` table rather than create a sibling :
- ``raw_ocr`` already stores OCR text — perfect for the dismissal text.
- ``seen_count`` already tracks occurrences — exactly what we need.
- ``source`` already supports 'ocr_arbitrage' / 'manual' — we extend with
  'llm' for entries authored by the LLM filter.
- ``type`` already partitions kinds — we add 'dismissal'.

Two changes :
1. ``type`` CHECK : add ``'dismissal'`` to the allowed enum.
2. New nullable column ``dismissal_category`` with a CHECK constraint to
   the fixed enum (payment_method / total / tva_label / footer /
   header_meta / fidelity / other). Required when type='dismissal',
   NULL otherwise — enforced at the application layer (a partial CHECK
   would over-constrain existing rows during downgrade).
3. ``source`` CHECK : add ``'llm'`` to the allowed enum.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260427_1000_dismissal"
down_revision = "20260426_1500_scans_price_nonneg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend type CHECK to include 'dismissal'.
    op.execute("ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS ck_ocr_knowledge_type")
    op.create_check_constraint(
        "ck_ocr_knowledge_type",
        "ocr_knowledge",
        "type IN ('product_name', 'brand_name', 'retailer_header', "
        "'address_token', 'dismissal')",
    )

    # 2. Extend source CHECK to include 'llm'.
    op.execute("ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS ck_ocr_knowledge_source")
    op.create_check_constraint(
        "ck_ocr_knowledge_source",
        "ocr_knowledge",
        "source IN ('ocr_arbitrage', 'user_correction', 'manual', 'llm')",
    )

    # 3. New nullable column for the dismissal category.
    op.add_column(
        "ocr_knowledge",
        sa.Column("dismissal_category", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_ocr_knowledge_dismissal_category",
        "ocr_knowledge",
        "dismissal_category IS NULL OR dismissal_category IN "
        "('payment_method', 'total', 'tva_label', 'footer', "
        "'header_meta', 'fidelity', 'other')",
    )


def downgrade() -> None:
    # Drop the dismissal column + its CHECK.
    op.execute(
        "ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS "
        "ck_ocr_knowledge_dismissal_category"
    )
    op.drop_column("ocr_knowledge", "dismissal_category")

    # Restore narrower source enum (will fail if any row has source='llm' —
    # operator must purge those rows first).
    op.execute("ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS ck_ocr_knowledge_source")
    op.create_check_constraint(
        "ck_ocr_knowledge_source",
        "ocr_knowledge",
        "source IN ('ocr_arbitrage', 'user_correction', 'manual')",
    )

    # Restore narrower type enum (will fail if any row has type='dismissal' —
    # operator must purge those rows first).
    op.execute("ALTER TABLE ocr_knowledge DROP CONSTRAINT IF EXISTS ck_ocr_knowledge_type")
    op.create_check_constraint(
        "ck_ocr_knowledge_type",
        "ocr_knowledge",
        "type IN ('product_name', 'brand_name', 'retailer_header', "
        "'address_token')",
    )
