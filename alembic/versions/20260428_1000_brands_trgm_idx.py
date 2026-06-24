"""products.brands GIN trgm index for Stage 2 pre-search (ARCH OCR↔LLM Bridge).

Revision ID: 20260428_1000_brands_trgm
Revises: 20260427_1900_partial_idx
Create Date: 2026-04-28 10:00:00.000000+00:00

Context : Stage 2 pre-search filter (cf ``worker.pipeline.matcher.find_candidates``)
runs ``products.brands ILIKE '%<brand>%'`` against ~1M rows on each LLM-bound
residue line. Without a trgm index this devolves to a sequential scan.

A GIN index on ``brands`` with ``gin_trgm_ops`` accelerates ILIKE substring
matches via trigram lookup. The ``products.name`` GIN trgm index already
exists (``gin_products_name`` in conftest, ``idx_products_name_trgm`` in
prod migration) — this brings ``brands`` to parity.

Idempotent : ``CREATE INDEX IF NOT EXISTS`` is a no-op on subsequent runs.
The pg_trgm extension is created defensively (no-op if already present).
"""
from __future__ import annotations

from alembic import op


revision = "20260428_1000_brands_trgm"
down_revision = "20260427_1900_partial_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_products_brands_trgm "
        "ON products USING gin (brands gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_products_brands_trgm")
