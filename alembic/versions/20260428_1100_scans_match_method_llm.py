"""scans.match_method — extend enum with 'llm' (ARCH OCR↔LLM Bridge Phase 3).

Revision ID: 20260428_1100_match_llm
Revises: 20260428_1000_brands_trgm
Create Date: 2026-04-28 11:00:00.000000+00:00

Phase 3 of the OCR↔LLM Bridge introduces an LLM-resolved EAN path. When
the LLM returns ``match_confidence`` in {high, medium} with a non-null
``matched_ean``, the worker writes that EAN to ``scans.product_ean`` and
marks ``match_method='llm'``. Existing values (observed_name, fuzzy,
fuzzy_confirmed, manual, barcode_ean) are preserved.

Idempotent : drops then recreates the CHECK constraint via ``IF EXISTS``.
"""
from __future__ import annotations

from alembic import op


revision = "20260428_1100_match_llm"
down_revision = "20260428_1000_brands_trgm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method "
        "CHECK (match_method IN ("
        "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', "
        "'barcode_ean', 'llm'"
        "))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method "
        "CHECK (match_method IN ("
        "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', "
        "'barcode_ean'"
        "))"
    )
