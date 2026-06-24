"""Apply HSP1 initial atoms (credit_cab / debit_cab / link_scan_to_user).

Revision ID: 20260519_1000_initial_atoms
Revises: 20260518_1600_db_write_apprv
Create Date: 2026-05-19 10:00:00.000000

HSP1 — premier lot de procedures stockees du catalogue. Chaque atome est
applique via `apply_procedure`, qui (a) charge `db/procedures/<name>.sql`,
(b) charge son manifeste sidecar `<name>.manifest.toml`, (c) lance le
verifier pglast, (d) execute le CREATE OR REPLACE PROCEDURE.

Cf :
    ARCH_n8n_pipelines.md § HSP-1 (catalogue d'atomes — décisions consolidées post-merge ; spec d'origine récupérable via git show 75cd3d15:docs/superpowers/specs/2026-05-19-db-pipeline-hsp1-catalogue-design.md)
"""
from __future__ import annotations

from alembic import op

from ratis_core.db_procedures import apply_procedure

# revision identifiers (≤32 chars per R08).
revision = "20260519_1000_initial_atoms"
down_revision = "20260518_1600_db_write_apprv"
branch_labels = None
depends_on = None


_ATOMS = (
    "support_credit_cab",
    "support_debit_cab",
    "support_link_scan_to_user",
)


def upgrade() -> None:
    for name in _ATOMS:
        apply_procedure(name)


def downgrade() -> None:
    # CREATE OR REPLACE => DROP IF EXISTS suffit. Les signatures sont fixes
    # (cf .sql / .manifest.toml). On ne DROP que les procedures de ce lot —
    # idempotent et reentrant.
    op.execute("DROP PROCEDURE IF EXISTS support_credit_cab(uuid, integer, integer)")
    op.execute("DROP PROCEDURE IF EXISTS support_debit_cab(uuid, integer, integer)")
    op.execute("DROP PROCEDURE IF EXISTS support_link_scan_to_user(uuid, uuid, integer)")
