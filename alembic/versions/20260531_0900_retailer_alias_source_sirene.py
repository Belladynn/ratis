"""Extend ck_retailer_aliases_source to include 'sirene' and 'overture'.

Revision ID: 20260531_0900_alias_src
Revises: 20260526_1000_apply_reset_stuck_route
Create Date: 2026-05-31
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260531_0900_alias_src"
down_revision = "20260526_1000_reset_stuck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE retailer_aliases DROP CONSTRAINT IF EXISTS ck_retailer_aliases_source")
    op.execute(
        "ALTER TABLE retailer_aliases ADD CONSTRAINT ck_retailer_aliases_source "
        "CHECK (source IN ('osm', 'sirene', 'overture', 'receipt_header', 'manual'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE retailer_aliases DROP CONSTRAINT IF EXISTS ck_retailer_aliases_source")
    op.execute(
        "ALTER TABLE retailer_aliases ADD CONSTRAINT ck_retailer_aliases_source "
        "CHECK (source IN ('osm', 'receipt_header', 'manual'))"
    )
