"""Matcher consensus-only refonte — drop scans.candidate_eans column.

Revision ID: 20260502_1500_dropce
Revises: 20260502_1200_afv1
Create Date: 2026-05-02 15:00:00

Following the matcher consensus-only refonte (2026-05-02 — see
``ARCH_name_resolution_consensus.md`` § "Philosophie") :

- The matcher no longer attempts whole-product fuzzy matching against
  ``products``. As a consequence it never produces fuzzy fallback
  candidates either.
- ``scans.candidate_eans`` was the storage for those candidates ; with
  no producer left it becomes dead data.

We drop the column outright. The corresponding model field and the
admin-queue ``top_candidates`` aggregation that read this column are
removed in the same PR.

Downgrade re-creates the column as a nullable ``JSONB`` so the schema
shape comes back ; populated state cannot be reconstructed (the
upgrade does not snapshot the old values).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260502_1500_dropce"
down_revision = "20260502_1200_afv1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE scans DROP COLUMN IF EXISTS candidate_eans")


def downgrade() -> None:
    op.add_column(
        "scans",
        sa.Column(
            "candidate_eans",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
    )
