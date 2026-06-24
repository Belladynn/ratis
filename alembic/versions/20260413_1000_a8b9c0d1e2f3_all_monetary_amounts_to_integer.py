"""all monetary amounts to integer centimes + cashback_transactions.parent_type

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-13 10:00:00.000000

Changes:
- cashback_transactions.amount      NUMERIC(10,2) → INTEGER (centimes, backfill ×100)
- cashback_withdrawals.amount       NUMERIC(10,2) → INTEGER (centimes, backfill ×100)
- user_cashback_balance.balance     NUMERIC(10,2) → INTEGER (centimes, backfill ×100)
- scans.price                       NUMERIC(10,2) → INTEGER (centimes, backfill ×100)
- scans.tva_amount                  NUMERIC(10,2) → INTEGER  nullable (centimes, backfill ×100)
- receipts.total_amount             NUMERIC(10,2) → INTEGER  nullable (centimes, backfill ×100)
- receipts.tva_total                NUMERIC(10,2) → INTEGER  nullable (centimes, backfill ×100)
- price_consensus.price             NUMERIC(10,2) → INTEGER (centimes, backfill ×100)
- price_consensus_history.price     NUMERIC(10,2) → INTEGER (centimes, backfill ×100)
- cashback_transactions: ADD COLUMN parent_type TEXT NULL
  CHECK (parent_type IN ('boost_parent', 'withdrawal_refund'))
  Semantics: non-NULL only when parent_transaction_id IS NOT NULL.
    'boost_parent'      → BOOST row pointing to its CREDIT affiliate parent
    'withdrawal_refund' → compensatory CREDIT pointing to the failed WITHDRAWAL
"""
from __future__ import annotations

from alembic import op


revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. cashback_transactions.amount → INTEGER
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE cashback_transactions "
        "DROP CONSTRAINT IF EXISTS cashback_transactions_amount_check"
    )
    op.execute(
        "ALTER TABLE cashback_transactions "
        "ALTER COLUMN amount TYPE INTEGER USING ROUND(amount * 100)::INTEGER"
    )
    op.execute(
        "ALTER TABLE cashback_transactions "
        "ADD CONSTRAINT cashback_transactions_amount_check CHECK (amount > 0)"
    )

    # ------------------------------------------------------------------
    # 2. cashback_transactions: ADD COLUMN parent_type
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE cashback_transactions ADD COLUMN parent_type TEXT"
    )
    op.execute(
        "ALTER TABLE cashback_transactions "
        "ADD CONSTRAINT cashback_transactions_parent_type_check "
        "CHECK (parent_type IN ('boost_parent', 'withdrawal_refund'))"
    )

    # ------------------------------------------------------------------
    # 3. cashback_withdrawals.amount → INTEGER
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "DROP CONSTRAINT IF EXISTS cashback_withdrawals_amount_check"
    )
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "ALTER COLUMN amount TYPE INTEGER USING ROUND(amount * 100)::INTEGER"
    )
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "ADD CONSTRAINT cashback_withdrawals_amount_check CHECK (amount > 0)"
    )

    # ------------------------------------------------------------------
    # 4. user_cashback_balance.balance → INTEGER
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE user_cashback_balance "
        "DROP CONSTRAINT IF EXISTS user_cashback_balance_balance_check"
    )
    op.execute(
        "ALTER TABLE user_cashback_balance "
        "ALTER COLUMN balance TYPE INTEGER USING ROUND(balance * 100)::INTEGER, "
        "ALTER COLUMN balance SET DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE user_cashback_balance "
        "ADD CONSTRAINT user_cashback_balance_balance_check CHECK (balance >= 0)"
    )

    # ------------------------------------------------------------------
    # 5. scans.price → INTEGER
    #    Must drop price_history VIEW first (depends on scans.price).
    # ------------------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS price_history")
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS price_pos"
    )
    op.execute(
        "ALTER TABLE scans "
        "ALTER COLUMN price TYPE INTEGER USING ROUND(price * 100)::INTEGER"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT price_pos CHECK (price > 0)"
    )
    # Recreate the view — price is now INTEGER centimes.
    op.execute(
        "CREATE VIEW price_history AS "
        "SELECT id AS observation_id, store_id, product_ean, price, quantity, "
        "       scan_type, scanned_name, scanned_at AS recorded_at "
        "FROM scans WHERE status = 'accepted'"
    )

    # ------------------------------------------------------------------
    # 6. scans.tva_amount → INTEGER nullable
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS tva_pos"
    )
    op.execute(
        "ALTER TABLE scans "
        "ALTER COLUMN tva_amount TYPE INTEGER "
        "USING CASE WHEN tva_amount IS NULL THEN NULL ELSE ROUND(tva_amount * 100)::INTEGER END"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT tva_pos CHECK (tva_amount IS NULL OR tva_amount >= 0)"
    )

    # ------------------------------------------------------------------
    # 7. receipts.total_amount → INTEGER nullable
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE receipts DROP CONSTRAINT IF EXISTS total_amount_pos"
    )
    op.execute(
        "ALTER TABLE receipts "
        "ALTER COLUMN total_amount TYPE INTEGER "
        "USING CASE WHEN total_amount IS NULL THEN NULL ELSE ROUND(total_amount * 100)::INTEGER END"
    )
    op.execute(
        "ALTER TABLE receipts "
        "ADD CONSTRAINT total_amount_pos CHECK (total_amount IS NULL OR total_amount > 0)"
    )

    # ------------------------------------------------------------------
    # 8. receipts.tva_total → INTEGER nullable
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE receipts DROP CONSTRAINT IF EXISTS tva_pos"
    )
    op.execute(
        "ALTER TABLE receipts "
        "ALTER COLUMN tva_total TYPE INTEGER "
        "USING CASE WHEN tva_total IS NULL THEN NULL ELSE ROUND(tva_total * 100)::INTEGER END"
    )
    op.execute(
        "ALTER TABLE receipts "
        "ADD CONSTRAINT tva_pos CHECK (tva_total IS NULL OR tva_total >= 0)"
    )

    # ------------------------------------------------------------------
    # 9. price_consensus.price → INTEGER
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE price_consensus DROP CONSTRAINT IF EXISTS price_pos"
    )
    op.execute(
        "ALTER TABLE price_consensus "
        "ALTER COLUMN price TYPE INTEGER USING ROUND(price * 100)::INTEGER"
    )
    op.execute(
        "ALTER TABLE price_consensus ADD CONSTRAINT price_pos CHECK (price > 0)"
    )

    # ------------------------------------------------------------------
    # 10. price_consensus_history.price → INTEGER
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE price_consensus_history DROP CONSTRAINT IF EXISTS price_pos"
    )
    op.execute(
        "ALTER TABLE price_consensus_history "
        "ALTER COLUMN price TYPE INTEGER USING ROUND(price * 100)::INTEGER"
    )
    op.execute(
        "ALTER TABLE price_consensus_history ADD CONSTRAINT price_pos CHECK (price > 0)"
    )


def downgrade() -> None:
    # price_consensus_history.price → NUMERIC(10,2)
    op.execute(
        "ALTER TABLE price_consensus_history DROP CONSTRAINT IF EXISTS price_pos"
    )
    op.execute(
        "ALTER TABLE price_consensus_history "
        "ALTER COLUMN price TYPE NUMERIC(10,2) USING (price / 100.0)"
    )
    op.execute(
        "ALTER TABLE price_consensus_history "
        "ADD CONSTRAINT price_pos CHECK (price > 0)"
    )

    # price_consensus.price → NUMERIC(10,2)
    op.execute("ALTER TABLE price_consensus DROP CONSTRAINT IF EXISTS price_pos")
    op.execute(
        "ALTER TABLE price_consensus "
        "ALTER COLUMN price TYPE NUMERIC(10,2) USING (price / 100.0)"
    )
    op.execute(
        "ALTER TABLE price_consensus ADD CONSTRAINT price_pos CHECK (price > 0)"
    )

    # receipts.tva_total → NUMERIC(10,2)
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS tva_pos")
    op.execute(
        "ALTER TABLE receipts "
        "ALTER COLUMN tva_total TYPE NUMERIC(10,2) "
        "USING CASE WHEN tva_total IS NULL THEN NULL ELSE tva_total / 100.0 END"
    )
    op.execute(
        "ALTER TABLE receipts "
        "ADD CONSTRAINT tva_pos CHECK (tva_total IS NULL OR tva_total >= 0)"
    )

    # receipts.total_amount → NUMERIC(10,2)
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS total_amount_pos")
    op.execute(
        "ALTER TABLE receipts "
        "ALTER COLUMN total_amount TYPE NUMERIC(10,2) "
        "USING CASE WHEN total_amount IS NULL THEN NULL ELSE total_amount / 100.0 END"
    )
    op.execute(
        "ALTER TABLE receipts "
        "ADD CONSTRAINT total_amount_pos CHECK (total_amount IS NULL OR total_amount > 0)"
    )

    # scans.tva_amount → NUMERIC(10,2)
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS tva_pos")
    op.execute(
        "ALTER TABLE scans "
        "ALTER COLUMN tva_amount TYPE NUMERIC(10,2) "
        "USING CASE WHEN tva_amount IS NULL THEN NULL ELSE tva_amount / 100.0 END"
    )
    op.execute(
        "ALTER TABLE scans "
        "ADD CONSTRAINT tva_pos CHECK (tva_amount IS NULL OR tva_amount >= 0)"
    )

    # scans.price → NUMERIC(10,2)
    op.execute("DROP VIEW IF EXISTS price_history")
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS price_pos")
    op.execute(
        "ALTER TABLE scans "
        "ALTER COLUMN price TYPE NUMERIC(10,2) USING (price / 100.0)"
    )
    op.execute("ALTER TABLE scans ADD CONSTRAINT price_pos CHECK (price > 0)")
    op.execute(
        "CREATE VIEW price_history AS "
        "SELECT id AS observation_id, store_id, product_ean, price, quantity, "
        "       scan_type, scanned_name, scanned_at AS recorded_at "
        "FROM scans WHERE status = 'accepted'"
    )

    # user_cashback_balance.balance → NUMERIC(10,2)
    op.execute(
        "ALTER TABLE user_cashback_balance "
        "DROP CONSTRAINT IF EXISTS user_cashback_balance_balance_check"
    )
    op.execute(
        "ALTER TABLE user_cashback_balance "
        "ALTER COLUMN balance TYPE NUMERIC(10,2) USING (balance / 100.0), "
        "ALTER COLUMN balance SET DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE user_cashback_balance "
        "ADD CONSTRAINT user_cashback_balance_balance_check CHECK (balance >= 0)"
    )

    # cashback_withdrawals.amount → NUMERIC(10,2)
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "DROP CONSTRAINT IF EXISTS cashback_withdrawals_amount_check"
    )
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "ALTER COLUMN amount TYPE NUMERIC(10,2) USING (amount / 100.0)"
    )
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "ADD CONSTRAINT cashback_withdrawals_amount_check CHECK (amount > 0)"
    )

    # cashback_transactions: DROP parent_type
    op.execute(
        "ALTER TABLE cashback_transactions "
        "DROP CONSTRAINT IF EXISTS cashback_transactions_parent_type_check"
    )
    op.execute(
        "ALTER TABLE cashback_transactions DROP COLUMN IF EXISTS parent_type"
    )

    # cashback_transactions.amount → NUMERIC(10,2)
    op.execute(
        "ALTER TABLE cashback_transactions "
        "DROP CONSTRAINT IF EXISTS cashback_transactions_amount_check"
    )
    op.execute(
        "ALTER TABLE cashback_transactions "
        "ALTER COLUMN amount TYPE NUMERIC(10,2) USING (amount / 100.0)"
    )
    op.execute(
        "ALTER TABLE cashback_transactions "
        "ADD CONSTRAINT cashback_transactions_amount_check CHECK (amount > 0)"
    )
