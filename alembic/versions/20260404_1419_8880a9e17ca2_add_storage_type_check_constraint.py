"""add_storage_type_check_constraint

Revision ID: 8880a9e17ca2
Revises: daab4a5938da
Create Date: 2026-04-04 14:19:28.537427+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8880a9e17ca2'
down_revision: Union[str, None] = 'daab4a5938da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 'fresh_meat' was a former intermediate value — normalize before adding constraint.
    op.execute("UPDATE products SET storage_type = 'fresh' WHERE storage_type = 'fresh_meat'")
    op.execute(
        "ALTER TABLE products ADD CONSTRAINT ck_products_storage_type "
        "CHECK (storage_type IN ('frozen', 'fresh', 'ambient', 'unmatched'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE products DROP CONSTRAINT ck_products_storage_type")
