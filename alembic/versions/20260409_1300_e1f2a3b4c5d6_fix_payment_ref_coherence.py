"""Fix payment_ref_coherence to allow NULL for cancelled-from-pending subscriptions

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-04-09 13:00:00.000000

The previous migration added OR status = 'pending' but that does not cover the case
where a pending subscription is cancelled (status='cancelled', payment_ref still NULL).
New constraint: paid_with='cashback' OR payment_ref IS NOT NULL OR status NOT IN ('active','expired')
"""
from __future__ import annotations

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT payment_ref_coherence")
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT payment_ref_coherence "
        "CHECK (paid_with = 'cashback' OR payment_ref IS NOT NULL OR status NOT IN ('active', 'expired'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT payment_ref_coherence")
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT payment_ref_coherence "
        "CHECK (paid_with = 'cashback' OR payment_ref IS NOT NULL OR status = 'pending')"
    )
