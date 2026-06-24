"""cabecoin_transactions: add stonks_boost and mission_freeze reasons

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-13 22:10:00
"""
from __future__ import annotations

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None

_NEW_CONSTRAINT = (
    "reason IN ("
    "'receipt_scan','label_scan','barcode_scan','mission_reward',"
    "'battlepass_milestone','referral',"
    "'cashback_boost_debit','cashback_boost_refund',"
    "'shop_purchase','stonks_boost','mission_freeze'"
    ")"
)

_OLD_CONSTRAINT = (
    "reason IN ("
    "'receipt_scan','label_scan','barcode_scan','mission_reward',"
    "'battlepass_milestone','referral',"
    "'cashback_boost_debit','cashback_boost_refund',"
    "'shop_purchase'"
    ")"
)

_OLD_REF_TYPE = (
    "reference_type IS NULL OR reference_type IN "
    "('scan', 'mission', 'battlepass_milestone', 'referral')"
)
_NEW_REF_TYPE = (
    "reference_type IS NULL OR reference_type IN "
    "('scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission')"
)

_DROP_REASON = (
    "ALTER TABLE cabecoin_transactions "
    "DROP CONSTRAINT IF EXISTS cabecoin_transactions_reason_check"
)
_DROP_REF = (
    "ALTER TABLE cabecoin_transactions "
    "DROP CONSTRAINT IF EXISTS cabecoin_transactions_reference_type_check"
)


def upgrade() -> None:
    op.execute(_DROP_REASON)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reason_check CHECK ({_NEW_CONSTRAINT})"
    )
    op.execute(_DROP_REF)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reference_type_check "
        f"CHECK ({_NEW_REF_TYPE})"
    )


def downgrade() -> None:
    op.execute(_DROP_REASON)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reason_check CHECK ({_OLD_CONSTRAINT})"
    )
    op.execute(_DROP_REF)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reference_type_check "
        f"CHECK ({_OLD_REF_TYPE})"
    )
