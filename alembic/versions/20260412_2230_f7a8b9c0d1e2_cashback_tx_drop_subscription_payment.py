"""cashback_transactions: remove unused SUBSCRIPTION_PAYMENT from type_check

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-12 22:30:00.000000

SUBSCRIPTION_PAYMENT was never used in any service or route.

Note: the type_check constraint may be named 'type_check' (initial migration
naming convention) or 'cashback_transactions_type_check' (SQLAlchemy expanded
naming). Both are dropped with IF EXISTS before recreating with the canonical
full name.
"""
from __future__ import annotations

from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None

_TYPES_NEW = "'CREDIT', 'BOOST', 'WITHDRAWAL'"
_TYPES_OLD = "'CREDIT', 'BOOST', 'WITHDRAWAL', 'SUBSCRIPTION_PAYMENT'"

_DROP = (
    "ALTER TABLE cashback_transactions DROP CONSTRAINT IF EXISTS cashback_transactions_type_check; "
    "ALTER TABLE cashback_transactions DROP CONSTRAINT IF EXISTS type_check"
)


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(
        f"ALTER TABLE cashback_transactions "
        f"ADD CONSTRAINT cashback_transactions_type_check CHECK (type IN ({_TYPES_NEW}))"
    )


def downgrade() -> None:
    op.execute(_DROP)
    op.execute(
        f"ALTER TABLE cashback_transactions "
        f"ADD CONSTRAINT cashback_transactions_type_check CHECK (type IN ({_TYPES_OLD}))"
    )
