"""Add CHECK (amount >= 0) on cashback_transactions.amount

Revision ID: h1i2j3k4l5m6
Revises: g9h0i1j2k3l4
Create Date: 2026-04-14 15:00:00

Changes:
  1. cashback_transactions — ADD CONSTRAINT ck_cashback_transactions_amount_nn
     CHECK (amount >= 0). No prod data yet — constraint safe to add immediately.
"""
from __future__ import annotations

from alembic import op

revision = "h1i2j3k4l5m6"
down_revision = "g9h0i1j2k3l4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_cashback_transactions_amount_nn",
        "cashback_transactions",
        "amount >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cashback_transactions_amount_nn",
        "cashback_transactions",
        type_="check",
    )
