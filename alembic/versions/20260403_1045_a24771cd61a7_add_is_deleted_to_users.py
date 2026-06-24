"""add_is_deleted_to_users

Revision ID: a24771cd61a7
Revises: c70458e93bcd
Create Date: 2026-04-03

RGPD tombstone flag — blocks authentication even if a valid JWT is presented.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a24771cd61a7"
down_revision: Union[str, Sequence[str], None] = "c70458e93bcd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "is_deleted")
