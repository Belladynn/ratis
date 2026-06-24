"""Apply HSP1 atom support_reset_stuck_optimized_route.

Revision ID: 20260526_1000_reset_stuck
Revises: 20260521_1100_hsp4_confine
Create Date: 2026-05-26 10:00:00.000000

Add the ``support_reset_stuck_optimized_route(p_list_id uuid)`` procedure to
the catalogue. Manual rescue path for the ``optimized_routes.status='computing'``
stuck-route class of bugs (Sentry RATIS-WEBSERVICES-18). The automatic
ghost-row reset (cf ``routes/optimization.py``) covers the common case ; this
procedure remains the operator-facing escape hatch when the automatic
threshold has not been reached or an operator wants to flush immediately.
"""
from __future__ import annotations

from alembic import op

from ratis_core.db_procedures import apply_procedure

# revision identifiers (≤32 chars per R08 — 29 chars).
revision = "20260526_1000_reset_stuck"
down_revision = "20260521_1100_hsp4_confine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply_procedure("support_reset_stuck_optimized_route")

    # HSP3 — seed trust_level=manual for the new atom in
    # app_settings.db_pipeline_trust_levels. JSONB merge via concat so we
    # preserve any operator-set overrides on the other atoms. The DEFAULT
    # value matches the manifest's ``trust_level_initial = "manual"``.
    op.execute(
        """
        UPDATE app_settings
        SET data = COALESCE(data, '{}'::jsonb)
                 || '{"support_reset_stuck_optimized_route": "manual"}'::jsonb
        WHERE section = 'db_pipeline_trust_levels'
        """
    )


def downgrade() -> None:
    # Remove the trust_level entry first (no-op if it was already pruned).
    op.execute(
        """
        UPDATE app_settings
        SET data = data - 'support_reset_stuck_optimized_route'
        WHERE section = 'db_pipeline_trust_levels'
        """
    )
    op.execute(
        "DROP PROCEDURE IF EXISTS support_reset_stuck_optimized_route(uuid, integer)"
    )
