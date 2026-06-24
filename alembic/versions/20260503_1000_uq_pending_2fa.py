"""admin_settings_audit — partial UNIQUE INDEX, one pending_2fa per section.

Revision ID: 20260503_1000_uq_p2fa
Revises: 20260502_2100_retroscan
Create Date: 2026-05-03 10:00:00

Security audit H2 fix : prevent multiple ``pending_2fa`` rows from co-
existing for the same ``section``. Without this constraint, three back-
to-back PUTs each tripping the magnitude check would leave three pending
rows open simultaneously — confusing the operator (which audit_id to
confirm ?) and creating a race window where two confirmations applied
in quick succession overwrite each other unpredictably.

The auto-cancel logic in ``services/admin/settings_service`` flips any
existing pending row to ``cancelled`` *before* inserting the new one ;
this index is the DB-level guard that catches a forgotten code path or
a future refactor that bypasses the service layer.

Defensive pattern (R07) : ``DROP INDEX IF EXISTS`` before creating so
the migration is idempotent on repeat upgrade runs.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260503_1000_uq_p2fa"
down_revision = "20260502_2100_retroscan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive : drop any pre-existing index of the same name before
    # creating it. Idempotent re-run safe.
    op.execute(
        "DROP INDEX IF EXISTS uq_admin_settings_audit_one_pending_per_section"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_admin_settings_audit_one_pending_per_section "
        "ON admin_settings_audit (section) "
        "WHERE status = 'pending_2fa'"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_admin_settings_audit_one_pending_per_section"
    )
