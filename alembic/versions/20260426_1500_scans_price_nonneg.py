"""scans.price — relax price_pos to allow placeholder 0.

Revision ID: 20260426_1500_scans_price_nonneg
Revises: 20260422_1800_ref_giftcard_elig
Create Date: 2026-04-26 15:00:00.000000+00:00

Label scans are persisted at upload time with ``price=0`` as a placeholder ;
the worker fills in the real price after OCR. The original ``price_pos``
CHECK was ``price > 0`` which blocked every label upload with a 500.

Loosens to ``price >= 0``. Real-price invariant (no zero-price for completed
scans) is enforced by the OCR pipeline at the application layer — it never
sets ``price=0`` on a non-pending scan.

Receipts go into the separate ``receipts`` table so they are not affected.
"""
from __future__ import annotations

from alembic import op


revision = "20260426_1500_scans_price_nonneg"
down_revision = "20260422_1800_ref_giftcard_elig"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE scans DROP CONSTRAINT IF EXISTS price_pos')
    op.execute('ALTER TABLE scans ADD CONSTRAINT price_pos CHECK (price >= 0)')


def downgrade() -> None:
    # Down: tighten back to >0. Will fail if any pending label scan has
    # price=0 — operator must clean those rows first.
    op.execute('ALTER TABLE scans DROP CONSTRAINT IF EXISTS price_pos')
    op.execute('ALTER TABLE scans ADD CONSTRAINT price_pos CHECK (price > 0)')
