"""anti_fraud_pr5 — receipts.rescan_attempts column

Revision ID: 20260511_1900_afpr5
Revises: 20260511_1700_afpr4
Create Date: 2026-05-11 19:00:00

PR5 of the anti-fraud receipt-pipeline sprint — adds the
``receipts.rescan_attempts`` integer counter so the user-facing
``POST /api/v1/scan/receipt/{receipt_id}/rescan`` endpoint can enforce
the ``pipeline.anti_fraud.rescan_max_attempts`` cap (default 3,
validated PO 2026-05-11).

Schema choices (cf ARCH_receipt_pipeline.md § "Implem sprint suggéré"
PR5) :

- ``INTEGER NOT NULL DEFAULT 0`` — every new receipt starts at 0 ; the
  ``UPDATE ... SET rescan_attempts = rescan_attempts + 1`` in the route
  is atomic at the row level (no race when two concurrent rescans
  contend), and legacy rows that pre-date this column are backfilled
  with ``0`` by the server-side default.
- No CHECK constraint on the column — the cap is enforced by the
  application (settings-driven) rather than the schema, so PO can
  tune ``rescan_max_attempts`` without a migration.

This migration is DDL-only — the route logic ships in the same PR but
is gated behind ``pipeline_v3.enabled`` (today ``false`` in prod, cf
``ratis_settings.json``). The column is therefore inert until the V3
rollout flips the flag.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic. ≤32 chars per R08.
revision = "20260511_1900_afpr5"
down_revision = "20260511_1700_afpr4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column(
            "rescan_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("receipts", "rescan_attempts")
