"""cabecoin_transactions: admin adjustment support — reason + reference_type + context

Revision ID: 20260430_1500_cabadmin
Revises: 20260430_1000_pipev3
Create Date: 2026-04-30 15:00:00

Context — ARCH_admin_endpoints.md PR2 (RW admin CAB adjustment).

Adds:
- ``'admin'`` to ``cabecoin_transactions.reference_type`` CHECK enum
  (manual admin mutations get a dedicated reference_type for audit
  ségrégation vs the regular flow scan/mission/etc.).
- ``'admin_adjustment'`` to ``cabecoin_transactions.reason`` CHECK enum
  (semantic value : "this row is a manual admin tweak, not a regular
  business event"). Used by POST /api/v1/admin/cab/adjustment.
- ``context`` JSONB column (nullable) — captures the operator handle
  and the human-readable reason of the admin operation. Carries the
  audit trail inside the transaction row itself rather than a separate
  table (clean & queryable ; R33 — pas de second mécanisme tant qu'un
  unique champ JSONB suffit pour l'alpha).

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R-mig-drop).

⚠ KP-08 — synchronise 3 sources in the SAME commit :
1. CHECK constraints (reason + reference_type) here
2. ``ratis_core.models.gamification`` — model CheckConstraint enums + context col
3. ``webservices/ratis_rewards/repositories/cab_repository.py`` —
   ``VALID_REASONS`` frozenset (must include 'admin_adjustment').
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260430_1500_cabadmin"
down_revision = "20260430_1000_pipev3"
branch_labels = None
depends_on = None


_OLD_REF_TYPE = (
    "reference_type IS NULL OR reference_type IN ("
    "'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
    "'community_challenge_milestone'"
    ")"
)
_NEW_REF_TYPE = (
    "reference_type IS NULL OR reference_type IN ("
    "'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
    "'community_challenge_milestone', 'admin'"
    ")"
)

_OLD_REASON = (
    "reason IN ("
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'mission_reward', 'battlepass_milestone', 'referral', "
    "'cashback_boost_debit', 'cashback_boost_refund', 'shop_purchase', "
    "'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', "
    "'challenge_milestone'"
    ")"
)
_NEW_REASON = (
    "reason IN ("
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'mission_reward', 'battlepass_milestone', 'referral', "
    "'cashback_boost_debit', 'cashback_boost_refund', 'shop_purchase', "
    "'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', "
    "'challenge_milestone', "
    "'mystery_product', "
    "'admin_adjustment'"
    ")"
)

_DROP_REF = (
    "ALTER TABLE cabecoin_transactions "
    "DROP CONSTRAINT IF EXISTS cabecoin_transactions_reference_type_check"
)
_DROP_REASON = (
    "ALTER TABLE cabecoin_transactions "
    "DROP CONSTRAINT IF EXISTS cabecoin_transactions_reason_check"
)


def upgrade() -> None:
    op.execute(_DROP_REF)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reference_type_check "
        f"CHECK ({_NEW_REF_TYPE})"
    )
    op.execute(_DROP_REASON)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reason_check "
        f"CHECK ({_NEW_REASON})"
    )
    op.add_column(
        "cabecoin_transactions",
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cabecoin_transactions", "context")
    op.execute(_DROP_REF)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reference_type_check "
        f"CHECK ({_OLD_REF_TYPE})"
    )
    op.execute(_DROP_REASON)
    op.execute(
        f"ALTER TABLE cabecoin_transactions "
        f"ADD CONSTRAINT cabecoin_transactions_reason_check "
        f"CHECK ({_OLD_REASON})"
    )
