"""cabecoin_transactions: rename cashback_unlock to cashback_boost_debit

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-12 22:00:00.000000

"""
from __future__ import annotations

from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None

_REASONS_NEW = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'mission_reward', 'battlepass_milestone', 'referral', "
    "'cashback_boost_debit', 'cashback_boost_refund', 'shop_purchase'"
)

_REASONS_OLD = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'mission_reward', 'battlepass_milestone', 'referral', "
    "'cashback_unlock', 'cashback_boost_refund', 'shop_purchase'"
)


def upgrade() -> None:
    op.execute(
        "UPDATE cabecoin_transactions SET reason = 'cashback_boost_debit' WHERE reason = 'cashback_unlock'"
    )
    op.drop_constraint("cabecoin_transactions_reason_check", "cabecoin_transactions", type_="check")
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_REASONS_NEW})",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE cabecoin_transactions SET reason = 'cashback_unlock' WHERE reason = 'cashback_boost_debit'"
    )
    op.drop_constraint("cabecoin_transactions_reason_check", "cabecoin_transactions", type_="check")
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_REASONS_OLD})",
    )
