"""Add gift_card_orders.cap_reserved_cents — fiscal-cap reservation (audit H4).

Revision ID: 20260518_1000_gc_cap_resv
Revises: 20260517_1600_gc_churned, 20260517_1700_optroute_uq
Create Date: 2026-05-18 10:00:00.000000

H4 — the DAS2 annual fiscal cap (1199 €) is now reserved at gift-card
issuance for all 4 flows. This column records, per order, how much cap it
reserved, so the release path (failure) can decrement the user's YTD
counter idempotently. NOT NULL DEFAULT 0 — existing rows back-fill to 0
(they predate the reservation model).

This migration also merges the two independent heads that existed at the
time of its creation (20260517_1600_gc_churned and 20260517_1700_optroute_uq
— both revise 20260517_1500_gc_refund).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260518_1000_gc_cap_resv"
down_revision = ("20260517_1600_gc_churned", "20260517_1700_optroute_uq")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gift_card_orders",
        sa.Column(
            "cap_reserved_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("gift_card_orders", "cap_reserved_cents")
