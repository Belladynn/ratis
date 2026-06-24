"""add match_method column to scans

Revision ID: a2f8b9c1d3e5
Revises: e4a7c2d8f901
Create Date: 2026-04-06 11:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a2f8b9c1d3e5'
down_revision: Union[str, None] = 'e4a7c2d8f901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("match_method", sa.Text(), nullable=True))
    op.execute("""
        ALTER TABLE scans
        ADD CONSTRAINT ck_scans_match_method
        CHECK (match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method")
    op.drop_column("scans", "match_method")
