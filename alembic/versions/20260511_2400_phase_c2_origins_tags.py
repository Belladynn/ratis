"""phase_c2_origins_tags — add products.origins_tags ARRAY[TEXT]

Phase C-2 of the missions sprint. Adds a nullable ``origins_tags`` column
to ``products`` so the PA reconciliation trigger can derive an
``attribute:french`` qualifier (mirror of Phase C-1 ``attribute:organic``)
and emit a dual ``trigger_action`` per scan.

The column is populated going forward by ``ratis_batch_off_sync`` (every
nightly run touches the row's ``origins_tags`` via the EXCLUDED clause).
Historical rows are filled by the one-shot ``ratis_batch_origins_backfill``
batch — see ``batch/ratis_batch_origins_backfill/ARCH_BATCH_ORIGINS_BACKFILL.md``.

No index : ``origins_tags`` is consumed downstream of an already-targeted
EAN lookup (``db.get(Product, ean)``), never as a search key. Adding a
GIN index would cost write amplification on every off_sync upsert with no
read benefit.

The 3 ``product_identification + attribute:french`` mission templates
remain ``is_active=false`` after this migration — they get re-flipped by
a separate one-row migration AFTER the prod backfill batch confirms
≥80% coverage (cf PROD_CHECKLIST.md § Missions Phase C-2 attribute:french).

Revision ID: 20260511_2400_c2org
Revises: 20260511_2100_c5pc
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260511_2400_c2org"
down_revision: Union[str, None] = "20260511_2100_c5pc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("origins_tags", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "origins_tags")
