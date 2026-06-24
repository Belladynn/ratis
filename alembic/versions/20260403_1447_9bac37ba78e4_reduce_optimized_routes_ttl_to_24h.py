"""reduce_optimized_routes_ttl_to_24h

Revision ID: 9bac37ba78e4
Revises: 9cf7a2f5b7c8
Create Date: 2026-04-03 14:47:46.085202+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9bac37ba78e4'
down_revision: Union[str, None] = '9cf7a2f5b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE optimized_routes "
        "ALTER COLUMN expires_at SET DEFAULT (now() + '24:00:00'::interval)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE optimized_routes "
        "ALTER COLUMN expires_at SET DEFAULT (now() + '48:00:00'::interval)"
    )
