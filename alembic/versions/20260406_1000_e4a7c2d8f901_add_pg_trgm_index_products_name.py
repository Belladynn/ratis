"""add pg_trgm index on products.name for fuzzy match

Revision ID: e4a7c2d8f901
Revises: 3b6f76173a34
Create Date: 2026-04-06 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e4a7c2d8f901'
down_revision: Union[str, None] = '3b6f76173a34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""
        CREATE INDEX IF NOT EXISTS gin_products_name
        ON products USING gin (name gin_trgm_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS gin_products_name")
