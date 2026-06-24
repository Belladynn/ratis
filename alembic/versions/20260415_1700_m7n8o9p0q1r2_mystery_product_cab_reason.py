"""add mystery_product to cabecoin_transactions reason check

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-04-15 17:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None

_OLD_REASONS = (
    "receipt_scan", "label_scan", "barcode_scan",
    "mission_reward", "battlepass_milestone", "referral",
    "cashback_boost_debit", "cashback_boost_refund", "shop_purchase",
    "stonks_boost", "mission_freeze",
    "food_reserve_purchase",
    "streak_repair",
    "challenge_milestone",
)

_NEW_REASONS = _OLD_REASONS + ("mystery_product",)


def _reason_check(reasons: tuple) -> str:
    values = ", ".join(f"'{r}'" for r in reasons)
    return f"reason IN ({values})"


def upgrade() -> None:
    op.drop_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        _reason_check(_NEW_REASONS),
    )


def downgrade() -> None:
    op.drop_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        _reason_check(_OLD_REASONS),
    )
