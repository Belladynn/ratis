"""add product_observed_names view

Revision ID: 3b6f76173a34
Revises: 28974f35fb76
Create Date: 2026-04-05 13:11:38.294110+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3b6f76173a34'
down_revision: Union[str, None] = '28974f35fb76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS product_observed_names")
    op.execute("""
        CREATE VIEW product_observed_names AS
        SELECT
            s.store_id,
            s.product_ean,
            s.scanned_name,
            COUNT(*) AS frequency
        FROM scans s
        WHERE s.status = 'accepted'
          AND s.product_ean IS NOT NULL
          AND s.scanned_name IS NOT NULL
        GROUP BY s.store_id, s.product_ean, s.scanned_name
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS product_observed_names")
