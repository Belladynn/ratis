"""reconciliation idempotency — unique partial indexes

Ensures write-side idempotency for ratis_batch_reconciliation under concurrent runs.

- cabecoin_transactions: one credit per scan (reference_type='scan', direction='credit')
- cashback_transactions: one CREDIT per (scan_id, product_ean)

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-04-15 18:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX uq_cabtx_scan_credit
        ON cabecoin_transactions(reference_id)
        WHERE direction = 'credit' AND reference_type = 'scan'
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_cashbacktx_scan_ean_credit
        ON cashback_transactions(scan_id, product_ean)
        WHERE type = 'CREDIT'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cashbacktx_scan_ean_credit")
    op.execute("DROP INDEX IF EXISTS uq_cabtx_scan_credit")
