"""cabecoin_transactions — add 'retro_scan' reference_type + reason.

Revision ID: 20260502_2100_retroscan
Revises: 9082f271f4d5
Create Date: 2026-05-02 21:00:00

Context — ``ratis_batch_data_reconciliation`` Phase 1 introduces Job 4
``retro_cab`` which credits CABs retroactively on scans newly resolved
by Job 1 ``ean_recovery``. To stay isolated from the financial batch
``ratis_batch_reconciliation`` (which writes ``reference_type='scan'``),
the data-reconciliation batch writes ``reference_type='retro_scan'``
with ``reason='retro_scan'``.

This migration extends the two CHECK constraints additively :

- ``cabecoin_transactions_reference_type_check`` : adds ``'retro_scan'``
- ``cabecoin_transactions_reason_check`` : adds ``'retro_scan'``

Idempotence at the application layer is enforced via a new partial
unique index ``uq_cabtx_retro_scan_credit`` on ``reference_id``
WHERE ``direction='credit' AND reference_type='retro_scan'`` so a
rerun of the batch after a crash cannot double-credit.

Defensive pattern (R07 / R-mig-drop) :
- ``ALTER TABLE ... DROP CONSTRAINT IF EXISTS`` before recreating
- ``DROP INDEX IF EXISTS`` before creating
"""
from __future__ import annotations

from alembic import op


revision = "20260502_2100_retroscan"
down_revision = "9082f271f4d5"
branch_labels = None
depends_on = None


_OLD_REF_TYPE_CHECK = (
    "reference_type IS NULL OR reference_type IN ("
    "'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
    "'community_challenge_milestone', 'admin'"
    ")"
)
_NEW_REF_TYPE_CHECK = (
    "reference_type IS NULL OR reference_type IN ("
    "'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
    "'community_challenge_milestone', 'admin', 'retro_scan'"
    ")"
)


_OLD_REASONS = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'mission_reward', 'battlepass_milestone', 'referral', "
    "'cashback_boost_debit', 'cashback_boost_refund', 'shop_purchase', "
    "'stonks_boost', 'mission_freeze', "
    "'food_reserve_purchase', 'streak_repair', "
    "'challenge_milestone', 'mystery_product', 'admin_adjustment'"
)
_NEW_REASONS = _OLD_REASONS + ", 'retro_scan'"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reference_type_check"
    )
    op.execute(
        "ALTER TABLE cabecoin_transactions ADD CONSTRAINT "
        "cabecoin_transactions_reference_type_check "
        f"CHECK ({_NEW_REF_TYPE_CHECK})"
    )

    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.execute(
        "ALTER TABLE cabecoin_transactions ADD CONSTRAINT "
        "cabecoin_transactions_reason_check "
        f"CHECK (reason IN ({_NEW_REASONS}))"
    )

    op.execute(
        "DROP INDEX IF EXISTS uq_cabtx_retro_scan_credit"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cabtx_retro_scan_credit "
        "ON cabecoin_transactions (reference_id) "
        "WHERE direction = 'credit' AND reference_type = 'retro_scan'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cabtx_retro_scan_credit")

    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    op.execute(
        "ALTER TABLE cabecoin_transactions ADD CONSTRAINT "
        "cabecoin_transactions_reason_check "
        f"CHECK (reason IN ({_OLD_REASONS}))"
    )

    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reference_type_check"
    )
    op.execute(
        "ALTER TABLE cabecoin_transactions ADD CONSTRAINT "
        "cabecoin_transactions_reference_type_check "
        f"CHECK ({_OLD_REF_TYPE_CHECK})"
    )
