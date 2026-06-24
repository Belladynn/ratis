"""cabecoin_transactions: add cashback_boost_refund to reason_check

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-12 16:00:00.000000

"""
from __future__ import annotations

from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("cabecoin_transactions_reason_check", "cabecoin_transactions", type_="check")
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'mission_reward', 'battlepass_milestone', 'referral', "
        "'cashback_unlock', 'cashback_boost_refund', 'shop_purchase')",
    )


def downgrade() -> None:
    op.drop_constraint("cabecoin_transactions_reason_check", "cabecoin_transactions", type_="check")
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        "reason IN ("
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'mission_reward', 'battlepass_milestone', 'referral', "
        "'cashback_unlock', 'shop_purchase')",
    )
