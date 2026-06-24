"""add_label_sessions

Revision ID: c3a1d7e2f84b
Revises: f4e3cd849eae
Create Date: 2026-04-06 16:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a1d7e2f84b"
down_revision: Union[str, None] = "f4e3cd849eae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "label_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stores.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.add_column(
        "scans",
        sa.Column(
            "label_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("label_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "scans",
        sa.Column("label_r2_key", sa.Text(), nullable=True),
    )

    op.create_index(
        "ix_scans_label_session_id",
        "scans",
        ["label_session_id"],
        postgresql_where=sa.text("label_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_scans_label_session_id", table_name="scans")
    op.drop_column("scans", "label_r2_key")
    op.drop_column("scans", "label_session_id")
    op.drop_table("label_sessions")
