"""NRC bloc C — partial index on pipeline_audit_log for consensus_state_changed.

Revision ID: 20260501_1700_nrcC
Revises: 20260501_1500_supid
Create Date: 2026-05-01 17:00:00

Speeds up ``was_ever_verified(store_id, normalized_label)`` queries by
indexing only the rows that actually carry consensus state transitions.
A partial index keeps the disk footprint minimal — at NRC's V1 scale
(receipts only emit one event per state change), the index is orders of
magnitude smaller than a full index on ``event``.

The index targets the exact predicate used by ``was_ever_verified`` :

    SELECT 1 FROM pipeline_audit_log
    WHERE event = 'consensus_state_changed'
      AND payload->>'store_id' = :store_id
      AND payload->>'normalized_label' = :label
      AND payload->>'to_state' = 'verified'
    LIMIT 1

Bloc C scope only — write paths land alongside.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260501_1700_nrcC"
down_revision = "20260501_1500_supid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_pal_consensus_state_changed",
        "pipeline_audit_log",
        [
            "event",
            sa.text("(payload->>'store_id')"),
            sa.text("(payload->>'normalized_label')"),
        ],
        postgresql_where=sa.text("event = 'consensus_state_changed'"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pal_consensus_state_changed")
