"""NRC bloc A — product_name_resolutions ledger + scans.candidate_eans

Revision ID: 20260501_1200_nrcA
Revises: 20260501_1000_offmf
Create Date: 2026-05-01 12:00:00

Foundation for the Name Resolution Consensus (NRC) refactor — see
``webservices/ratis_product_analyser/ARCH_name_resolution_consensus.md``.

Adds an append-only ledger ``product_name_resolutions`` for crowdsourced
``(store_id, normalized_label) → product_ean`` validations, and a
``scans.candidate_eans`` JSONB column to carry the top-3 fuzzy fallback
candidates when no consensus exists yet (read-only, populated by bloc B).

Bloc A scope : schema only — no runtime wiring. Write functions land in
bloc C (``record_resolution`` + barcode/admin hooks).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260501_1200_nrcA"
down_revision = "20260501_1000_offmf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_name_resolutions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stores.id"),
            nullable=False,
        ),
        sa.Column("normalized_label", sa.Text(), nullable=False),
        sa.Column("product_ean", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "match_method IN ('barcode', 'manual_admin', 'fuzzy_pending', 'observed_name')",
            name="pnr_match_method_check",
        ),
    )
    op.create_index(
        "idx_pnr_consensus",
        "product_name_resolutions",
        ["store_id", "normalized_label"],
    )
    op.create_index(
        "idx_pnr_scan_label",
        "product_name_resolutions",
        ["scan_id", "normalized_label"],
        unique=True,
    )
    op.create_index(
        "idx_pnr_user",
        "product_name_resolutions",
        ["user_id"],
    )

    # ``scans.candidate_eans`` — top-3 fuzzy fallback EANs (bloc B).
    # ``none_as_null=True`` so Python None → SQL NULL (consistent with
    # ``receipts.barcode_fields`` pattern, see scan.py model comment).
    op.add_column(
        "scans",
        sa.Column(
            "candidate_eans",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scans DROP COLUMN IF EXISTS candidate_eans")
    op.execute("DROP INDEX IF EXISTS idx_pnr_user")
    op.execute("DROP INDEX IF EXISTS idx_pnr_scan_label")
    op.execute("DROP INDEX IF EXISTS idx_pnr_consensus")
    op.drop_table("product_name_resolutions")
