"""add_total_lines_detected_to_receipts

Revision ID: d5e2f1a3b7c9
Revises: c3a1d7e2f84b
Create Date: 2026-04-07 15:30:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e2f1a3b7c9"
down_revision: Union[str, None] = "c3a1d7e2f84b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column("total_lines_detected", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("receipts", "total_lines_detected")
