"""boutique v1 — gift_card_purchase reason + ytd cents column + seed 5 brands.

Revision ID: 20260508_2200_boutiquev1
Revises: 20260508_2000_bp_s1
Create Date: 2026-05-08 22:00:00

Boutique V1 (cf ``webservices/ratis_rewards/ARCH_boutique.md``) ships
the user-initiated ``POST /api/v1/rewards/gift-cards/order`` endpoint.
This migration prepares the schema and catalogue :

1. Extend ``cabecoin_transactions.reason`` CHECK with
   ``'gift_card_purchase'`` — the new debit reason emitted by the
   boutique service when a user spends CAB on a gift card. Distinct
   from the legacy ``'shop_purchase'`` (kept for historical rows + the
   ``gift_card_orders.source_type`` enum).

2. Add ``users.gift_card_redeemed_ytd_cents`` (NOT NULL DEFAULT 0) :
   denormalised cumulative redemption count for the fiscal cap (1199 €/an
   DAS2, cf ARCH_cab_economy § Plafond annuel). Reset annually by the
   1st-of-January batch (out of scope here).

3. Add UNIQUE constraint on ``gift_card_brands.name`` so the boutique
   seed is idempotent via ``ON CONFLICT (name) DO NOTHING`` and the
   admin UI cannot create duplicate human-readable names.

4. Seed five Saison 1 brands (Amazon.fr · Carrefour · Decathlon ·
   Sephora · Spotify). The ``provider_brand_id`` values shipped here
   are explicit placeholders (``placeholder-runa-<slug>``) — operations
   substitute the real Runa product IDs after KYB validation. The
   placeholder prefix is grep-friendly so ``SELECT * FROM gift_card_brands
   WHERE provider_brand_id LIKE 'placeholder-runa-%'`` lists the rows
   still pending substitution.

⚠ KP-08 — synchronise 3 sources in the SAME commit :

- CHECK constraint here (DB)
- ``ratis_core.models.gamification._CAB_REASONS`` (ORM tuple)
- ``webservices/ratis_rewards/repositories/cab_repository.VALID_REASONS``
  (Python frozenset used by award_cab/debit_cab)

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R-mig-drop) so the
upgrade is idempotent on a re-run.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from ratis_core.seed.boutique_brands import seed_boutique_brands


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260508_2200_boutiquev1"
down_revision = "20260508_2000_bp_s1"
branch_labels = None
depends_on = None


# Reason set BEFORE this migration (matches 20260508_1800_missions_phase_b).
_OLD_REASON = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'product_identification', 'fill_product_field', 'scan_distinct', "
    "'promo_found', 'mission_reward', 'battlepass_milestone', "
    "'referral', 'cashback_boost_debit', 'cashback_boost_refund', "
    "'shop_purchase', 'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', 'challenge_milestone', "
    "'mystery_product', 'admin_adjustment', 'retro_scan'"
)
# New set — adds 'gift_card_purchase' for the boutique debit reason.
_NEW_REASON = _OLD_REASON + ", 'gift_card_purchase'"


def upgrade() -> None:
    # 1. Extend cabecoin_transactions.reason CHECK.
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_NEW_REASON})",
    )

    # 2. Add users.gift_card_redeemed_ytd_cents (denormalised yearly cap).
    op.add_column(
        "users",
        sa.Column(
            "gift_card_redeemed_ytd_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Drop the server_default after the backfill so application-level
    # mutations are explicit (every UPDATE writes the value). Mirrors the
    # pattern used for users.trust_score in 20260502_1200_anti_fraud_v1.
    op.alter_column(
        "users",
        "gift_card_redeemed_ytd_cents",
        server_default=None,
    )

    # 3. UNIQUE on gift_card_brands.name so the seed is idempotent.
    # Defensive : drop first (no-op on fresh DB) so a re-run upgrade is safe.
    op.execute(
        "ALTER TABLE gift_card_brands DROP CONSTRAINT IF EXISTS "
        "uq_gift_card_brands_name"
    )
    op.create_unique_constraint(
        "uq_gift_card_brands_name", "gift_card_brands", ["name"]
    )

    # 4. Seed Saison 1 catalogue. Idempotent via ON CONFLICT (name).
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        seed_boutique_brands(session)
        session.flush()
    finally:
        session.close()


def downgrade() -> None:
    # 4'. Wipe seeded brands (only those with the placeholder provider id —
    #      ops may have substituted the real id, in which case we leave the
    #      row alone to avoid destroying production catalogue state).
    op.execute(
        "DELETE FROM gift_card_brands "
        "WHERE provider_brand_id LIKE 'placeholder-runa-%' "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM gift_card_orders WHERE brand_id = gift_card_brands.id"
        "  )"
    )

    # 3'. Drop UNIQUE constraint.
    op.execute(
        "ALTER TABLE gift_card_brands DROP CONSTRAINT IF EXISTS "
        "uq_gift_card_brands_name"
    )

    # 2'. Drop ytd column.
    op.drop_column("users", "gift_card_redeemed_ytd_cents")

    # 1'. Restore old reason CHECK. If a 'gift_card_purchase' row already
    #     exists, the constraint creation will fail loudly (desired —
    #     downgrading past this point with real boutique activity is a
    #     destructive operation and needs explicit operator decision, R05).
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_OLD_REASON})",
    )
