"""scans + pipeline_audit_log: PA admin override support — manual_admin + manual phase

Revision ID: 20260430_1700_paadmin
Revises: 20260430_1500_cabadmin
Create Date: 2026-04-30 17:00:00

Context — ARCH_admin_endpoints.md PR3 (PA admin scan override).

Adds:
- ``'manual_admin'`` to ``scans.match_method`` CHECK enum
  (CK ck_scans_match_method_v3). Used by PATCH /api/v1/admin/scans/{id}
  when an operator force-attaches an EAN to an unresolved/rejected scan.
- ``'manual'`` to ``pipeline_audit_log.phase`` CHECK enum
  (CK ck_pipeline_audit_log_phase). Used to flag admin-originated audit
  events alongside the four pipeline phases (extract/comprehend/match/persist).

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R-mig-drop).

KP-08 — synchronise 3 sources in the SAME commit :
1. CHECK constraints here
2. ``ratis_core.models.scan`` — Scan CheckConstraint inline list
3. ``ratis_core.models.pipeline_v3`` — PipelineAuditLog CheckConstraint phase list
"""
from __future__ import annotations

from alembic import op

revision = "20260430_1700_paadmin"
down_revision = "20260430_1500_cabadmin"
branch_labels = None
depends_on = None


_OLD_MATCH_METHOD = (
    "match_method IS NULL OR match_method IN ("
    "'barcode', 'knowledge', 'fuzzy_strict', "
    "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean'"
    ")"
)
_NEW_MATCH_METHOD = (
    "match_method IS NULL OR match_method IN ("
    "'barcode', 'knowledge', 'fuzzy_strict', 'manual_admin', "
    "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean'"
    ")"
)

_OLD_AUDIT_PHASE = "phase IN ('extract', 'comprehend', 'match', 'persist')"
_NEW_AUDIT_PHASE = "phase IN ('extract', 'comprehend', 'match', 'persist', 'manual')"


def upgrade() -> None:
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method_v3")
    op.execute(
        f"ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method_v3 CHECK ({_NEW_MATCH_METHOD})"
    )
    op.execute(
        "ALTER TABLE pipeline_audit_log DROP CONSTRAINT IF EXISTS ck_pipeline_audit_log_phase"
    )
    op.execute(
        "ALTER TABLE pipeline_audit_log ADD CONSTRAINT ck_pipeline_audit_log_phase "
        f"CHECK ({_NEW_AUDIT_PHASE})"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method_v3")
    op.execute(
        f"ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method_v3 CHECK ({_OLD_MATCH_METHOD})"
    )
    op.execute(
        "ALTER TABLE pipeline_audit_log DROP CONSTRAINT IF EXISTS ck_pipeline_audit_log_phase"
    )
    op.execute(
        "ALTER TABLE pipeline_audit_log ADD CONSTRAINT ck_pipeline_audit_log_phase "
        f"CHECK ({_OLD_AUDIT_PHASE})"
    )
