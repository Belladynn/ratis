"""pipeline_v3 barcode port (PR-A) — convert receipts.barcode_fields json→jsonb + indexes on stores.store_code

Revision ID: 20260430_1900_v3barcode
Revises: 20260430_1700_paadmin
Create Date: 2026-04-30 19:00:00

Foundation step for the v3 barcode port (cf. ARCH_receipt_pipeline.md).

Operations :
1. Convert ``receipts.barcode_fields`` from ``json`` to ``jsonb`` so we can
   index / query inside the column from Phase 2/3 without a per-row cast.
   The model side already declares ``JSONB`` (see
   ``ratis_core.models.scan.Receipt.barcode_fields``) — this aligns the
   physical column with the ORM contract.
2. Add a partial composite index on ``stores(retailer, store_code)`` to
   support the upcoming Phase-3 store_match_by_code lookup.
3. Add a partial simple index on ``stores(store_code)`` for cross-retailer
   stats / debug lookups.

Both indexes are partial (``WHERE store_code IS NOT NULL``) — most rows
in OSM-sourced ``stores`` carry NULL ``store_code``, so the partial form
keeps the index small and dense on the rows that matter.

Defensive pattern uses ``DROP INDEX IF EXISTS`` (R-mig-drop).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260430_1900_v3barcode"
down_revision = "20260430_1700_paadmin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Convert json → jsonb (cast safely — json values are valid jsonb).
    op.execute(
        "ALTER TABLE receipts ALTER COLUMN barcode_fields "
        "TYPE jsonb USING barcode_fields::jsonb"
    )

    # 2. Composite partial index : (retailer, store_code) for Phase-3 lookup.
    op.create_index(
        "ix_stores_retailer_store_code",
        "stores",
        ["retailer", "store_code"],
        postgresql_where=sa.text("store_code IS NOT NULL AND retailer IS NOT NULL"),
    )

    # 3. Simple partial index on store_code (cross-retailer stats / debug).
    op.create_index(
        "ix_stores_store_code",
        "stores",
        ["store_code"],
        postgresql_where=sa.text("store_code IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_stores_store_code")
    op.execute("DROP INDEX IF EXISTS ix_stores_retailer_store_code")
    op.execute(
        "ALTER TABLE receipts ALTER COLUMN barcode_fields "
        "TYPE json USING barcode_fields::json"
    )
