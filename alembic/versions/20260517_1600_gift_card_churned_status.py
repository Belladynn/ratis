"""add_churned_to_gift_card_orders_status

Revision ID: 20260517_1600_gc_churned
Revises: 20260517_1500_gc_refund
Create Date: 2026-05-17 16:00:00.000000+00:00

Audit H3 — add ``'churned'`` to the ``gift_card_orders.status`` CHECK
constraint so that churn cancellations are distinguishable from real Runa
issuance failures (``'failed'``) in anti-fraud / fiscal audits.

Before this migration ``mark_churned`` was forced to write ``status='failed'``
because the constraint only allowed ``('pending', 'issued', 'failed')``. The
two terminal states were semantically different but looked identical in the DB.

After this migration ``mark_churned`` writes ``status='churned'`` (a distinct,
queryable terminal state) while genuine Runa failures keep ``status='failed'``.

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R07) so the upgrade is
idempotent on a re-run.
"""
from __future__ import annotations

from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260517_1600_gc_churned"
down_revision = "20260517_1500_gc_refund"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gift_card_orders DROP CONSTRAINT IF EXISTS "
        "ck_gift_card_orders_status"
    )
    op.create_check_constraint(
        "ck_gift_card_orders_status",
        "gift_card_orders",
        "status IN ('pending', 'issued', 'failed', 'churned')",
    )


def downgrade() -> None:
    # If a 'churned' row already exists, the constraint creation below will
    # fail loudly (desired — downgrading with real churn activity is
    # destructive and needs explicit operator decision, R05).
    op.execute(
        "ALTER TABLE gift_card_orders DROP CONSTRAINT IF EXISTS "
        "ck_gift_card_orders_status"
    )
    op.create_check_constraint(
        "ck_gift_card_orders_status",
        "gift_card_orders",
        "status IN ('pending', 'issued', 'failed')",
    )
