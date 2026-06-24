"""Add status column to optimized_routes for async task tracking."""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "c5d4e3f2a1b0"
down_revision = "b4c3d2e1f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimized_routes",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="ready",
        ),
    )
    op.create_check_constraint(
        "ck_optimized_routes_status",
        "optimized_routes",
        "status IN ('ready', 'computing', 'updating', 'failed')",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE optimized_routes DROP CONSTRAINT IF EXISTS ck_optimized_routes_status")
    op.execute("ALTER TABLE optimized_routes DROP COLUMN IF EXISTS status")
