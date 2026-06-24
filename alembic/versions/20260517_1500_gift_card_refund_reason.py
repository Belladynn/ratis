"""add_gift_card_refund_cab_reason

Revision ID: 20260517_1500_gc_refund
Revises: 20260517_1400_stripe_evt
Create Date: 2026-05-17 15:00:00.000000+00:00

Audit C3 — extend ``cabecoin_transactions.reason`` CHECK constraint to
accept ``'gift_card_refund'``.

When a boutique gift-card order fails (Runa returns FAILED or a network
error occurs), the user's debited CAB is now refunded via a credit row
with reason ``'gift_card_refund'``. Without this migration the INSERT
would fail with an IntegrityError because the existing
``cabecoin_transactions_reason_check`` rejects the new value.

⚠ KP-08 — synchronise 3 sources :

- CHECK constraint here (DB) — this migration.
- ``ratis_core.models.gamification._CAB_REASONS`` (updated in same commit).
- ``webservices/ratis_rewards/repositories/cab_repository.VALID_REASONS``
  (updated in same commit).

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R07) so the
upgrade is idempotent on a re-run.
"""
from __future__ import annotations

from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260517_1500_gc_refund"
down_revision = "20260517_1400_stripe_evt"
branch_labels = None
depends_on = None


# Reason set BEFORE this migration — matches 20260510_1100_ach_unlock_rsn
# (last migration that touched the constraint).
_OLD_REASON = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'product_identification', 'fill_product_field', 'scan_distinct', "
    "'promo_found', 'mission_reward', 'battlepass_milestone', "
    "'referral', 'cashback_boost_debit', 'cashback_boost_refund', "
    "'shop_purchase', 'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', 'challenge_milestone', "
    "'mystery_product', 'admin_adjustment', 'retro_scan', "
    "'gift_card_purchase', 'achievement_unlock'"
)
# New set — adds 'gift_card_refund' for audit C3 CAB refund on failed
# gift-card issuance. Placed between gift_card_purchase and
# achievement_unlock to mirror _CAB_REASONS ordering.
_NEW_REASON = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'product_identification', 'fill_product_field', 'scan_distinct', "
    "'promo_found', 'mission_reward', 'battlepass_milestone', "
    "'referral', 'cashback_boost_debit', 'cashback_boost_refund', "
    "'shop_purchase', 'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', 'challenge_milestone', "
    "'mystery_product', 'admin_adjustment', 'retro_scan', "
    "'gift_card_purchase', 'gift_card_refund', 'achievement_unlock'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_NEW_REASON})",
    )


def downgrade() -> None:
    # If a 'gift_card_refund' row already exists, the constraint creation
    # below will fail loudly (desired — downgrading with real refund activity
    # is destructive and needs explicit operator decision, R05).
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_OLD_REASON})",
    )
