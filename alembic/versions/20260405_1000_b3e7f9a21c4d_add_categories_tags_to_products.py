"""add_categories_tags_to_products

Revision ID: b3e7f9a21c4d
Revises: 8880a9e17ca2
Create Date: 2026-04-05 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3e7f9a21c4d'
down_revision: Union[str, None] = '8880a9e17ca2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('categories_tags', postgresql.ARRAY(sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'categories_tags')
