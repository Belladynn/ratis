"""add_xp_transactions_reference_checks

Revision ID: 20260515_1400_xp_ref_chk
Revises: 20260515_1200_postgisgeo
Create Date: 2026-05-15 14:00:00.000000

Audit RW-04 — ``xp_transactions`` had NEITHER a ``reference_type``
allowlist CHECK NOR a ``(reference_id IS NULL) = (reference_type IS NULL)``
consistency CHECK, unlike its parallel ledger table
``cabecoin_transactions``. XP rows could carry arbitrary / inconsistent
reference metadata — schema drift between two ledger tables.

This migration adds the two missing CHECK constraints, using the SAME
``reference_type`` literal set as ``cabecoin_transactions_reference_type_check``
(PO decision : XP allowlist == CAB allowlist). KP-08 multi-place sync
applies — any literal added to either CHECK must stay in sync.
"""
from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260515_1400_xp_ref_chk"
down_revision = "20260515_1200_postgisgeo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE xp_transactions
        DROP CONSTRAINT IF EXISTS xp_transactions_reference_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE xp_transactions
        ADD CONSTRAINT xp_transactions_reference_type_check
        CHECK (
            reference_type IS NULL
            OR reference_type IN (
                'scan', 'mission', 'battlepass_milestone', 'referral',
                'user_mission', 'community_challenge_milestone', 'admin',
                'retro_scan', 'achievement'
            )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE xp_transactions
        DROP CONSTRAINT IF EXISTS xp_transactions_reference_consistency_check
        """
    )
    op.execute(
        """
        ALTER TABLE xp_transactions
        ADD CONSTRAINT xp_transactions_reference_consistency_check
        CHECK ((reference_id IS NULL) = (reference_type IS NULL))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE xp_transactions
        DROP CONSTRAINT IF EXISTS xp_transactions_reference_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE xp_transactions
        DROP CONSTRAINT IF EXISTS xp_transactions_reference_consistency_check
        """
    )
