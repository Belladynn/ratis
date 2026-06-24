"""add is_template to shopping_lists

Revision ID: b4c3d2e1f5a6
Revises: a3b2c1d0e4f5
Create Date: 2026-04-16 20:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b4c3d2e1f5a6"
down_revision = "a3b2c1d0e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shopping_lists",
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("shopping_lists", "is_template")
