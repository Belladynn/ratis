"""add_achievement_unlock_reason

Revision ID: 20260510_1100_ach_unlock_rsn
Revises: 20260510_1030_ach_seed
Create Date: 2026-05-10 11:00:00.000000

Achievements V1 — extend ``cabecoin_transactions.reason`` CHECK constraint
to accept ``'achievement_unlock'``.

Sibling of the earlier ``20260510_1020_ach_cab_ref`` migration (which extended
``cabecoin_transactions_reference_type_check`` with ``'achievement'``). The
``reason`` constraint was missed in PR1 — without this migration, the FIRST
``_unlock()`` call in prod fails with ``IntegrityError`` because the existing
``cabecoin_transactions_reason_check`` rejects ``'achievement_unlock'``.

⚠ KP-08 — synchronise 3 sources :

- CHECK constraint here (DB) — this migration.
- ``ratis_core.models.gamification._CAB_REASONS`` (already shipped in PR1).
- ``webservices/ratis_rewards/repositories/cab_repository.VALID_REASONS``
  (already shipped in PR1).

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R-mig-drop) so the
upgrade is idempotent on a re-run.
"""
from __future__ import annotations

from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260510_1100_ach_unlock_rsn"
down_revision = "20260510_1030_ach_seed"
branch_labels = None
depends_on = None


# Reason set BEFORE this migration — matches 20260508_2200_boutique_v1
# (last migration that touched the constraint).
_OLD_REASON = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'product_identification', 'fill_product_field', 'scan_distinct', "
    "'promo_found', 'mission_reward', 'battlepass_milestone', "
    "'referral', 'cashback_boost_debit', 'cashback_boost_refund', "
    "'shop_purchase', 'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', 'challenge_milestone', "
    "'mystery_product', 'admin_adjustment', 'retro_scan', "
    "'gift_card_purchase'"
)
# New set — adds 'achievement_unlock' for the achievements V1 unlock credit.
_NEW_REASON = _OLD_REASON + ", 'achievement_unlock'"


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
    # If a 'achievement_unlock' row already exists, the constraint creation
    # below will fail loudly (desired — downgrading past this point with
    # real achievement activity is destructive and needs explicit operator
    # decision, R05).
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_OLD_REASON})",
    )
