"""add_cab_reason_achievement

Revision ID: 20260510_1020_ach_cab_ref
Revises: 20260510_1000_ach_v1
Create Date: 2026-05-10 10:20:00.000000

Achievements V1 — extend ``cabecoin_transactions.reference_type`` CHECK
constraint to allow ``'achievement'``.

Pre-migration values (cf 20260507_1000_… reconciliation hardening) :
    'scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission',
    'community_challenge_milestone', 'admin', 'retro_scan'

Post-migration adds : 'achievement'.

Constraint name : ``cabecoin_transactions_reference_type_check``
(original CHECK on the table — confirmed via ``\\d cabecoin_transactions``).
"""
from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260510_1020_ach_cab_ref"
down_revision = "20260510_1000_ach_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE cabecoin_transactions
        DROP CONSTRAINT IF EXISTS cabecoin_transactions_reference_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE cabecoin_transactions
        ADD CONSTRAINT cabecoin_transactions_reference_type_check
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


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE cabecoin_transactions
        DROP CONSTRAINT IF EXISTS cabecoin_transactions_reference_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE cabecoin_transactions
        ADD CONSTRAINT cabecoin_transactions_reference_type_check
        CHECK (
            reference_type IS NULL
            OR reference_type IN (
                'scan', 'mission', 'battlepass_milestone', 'referral',
                'user_mission', 'community_challenge_milestone', 'admin',
                'retro_scan'
            )
        )
        """
    )
