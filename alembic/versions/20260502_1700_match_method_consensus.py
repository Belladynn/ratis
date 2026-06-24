"""scans.match_method — add 'consensus_match' (pipeline_v3 consensus-only refonte).

Revision ID: 20260502_1700_consmatch
Revises: 20260502_1500_dropce
Create Date: 2026-05-02 17:00:00

Context — refonte 2026-05-02 du matcher pipeline_v3 (cf.
``ARCH_name_resolution_consensus.md`` § "Philosophie" et
``ARCH_receipt_pipeline.md`` § Phase 3). pipeline_v3 abandonne le
fuzzy product-level (``fuzzy_strict``) au profit d'une cascade
strictement consensus-only :

    barcode → knowledge curated → consensus exact → consensus fuzzy → STOP

La nouvelle méthode ``consensus_match`` apparaît sur ``scans.match_method``
quand le matcher résout un item via le ledger ``product_name_resolutions``
(état ``VERIFIED``). Les valeurs ``barcode`` / ``knowledge`` /
``fuzzy_strict`` restent dans le CHECK pour back-compat (PR follow-up
plus tard pour drop ``fuzzy_strict`` une fois la data migrée).

Defensive pattern uses ``DROP CONSTRAINT IF EXISTS`` (R-mig-drop).
"""
from __future__ import annotations

from alembic import op


revision = "20260502_1700_consmatch"
down_revision = "20260502_1500_dropce"
branch_labels = None
depends_on = None


_OLD_MATCH_METHOD = (
    "match_method IS NULL OR match_method IN ("
    "'barcode', 'knowledge', 'fuzzy_strict', 'manual_admin', "
    "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean'"
    ")"
)
_NEW_MATCH_METHOD = (
    "match_method IS NULL OR match_method IN ("
    "'barcode', 'knowledge', 'consensus_match', 'fuzzy_strict', 'manual_admin', "
    "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean'"
    ")"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method_v3"
    )
    op.execute(
        f"ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method_v3 CHECK ({_NEW_MATCH_METHOD})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method_v3"
    )
    op.execute(
        f"ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method_v3 CHECK ({_OLD_MATCH_METHOD})"
    )
