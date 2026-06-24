"""Add ref_lat/ref_lng to users for approximate home location."""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "a3b2c1d0e4f5"
down_revision = "d14fe7c0cb81"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ref_lat", sa.Numeric(9, 3), nullable=True))
    op.add_column("users", sa.Column("ref_lng", sa.Numeric(9, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ref_lng")
    op.drop_column("users", "ref_lat")
