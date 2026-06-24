"""Add db_write_approvals — table miroir du gate d'approbation DB (SP6).

Revision ID: 20260518_1600_db_write_apprv
Revises: 20260518_1400_pushrcpt
Create Date: 2026-05-18 16:00:00.000000

SP6 — le workflow n8n ``db-write-pipeline`` enregistre ici chaque
proposition d'écriture atteignant le gate humain. L'UI admin
``/admin/ui/db-approvals`` lit cette table ; la décision (statut +
opérateur + motif) y est persistée durablement. Voir
``docs/superpowers/specs/2026-05-18-db-approval-ui-sp6-design.md``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers (≤32 chars per R08).
revision = "20260518_1600_db_write_apprv"
down_revision = "20260518_1400_pushrcpt"
branch_labels = None
depends_on = None


_APPROVAL_STATUS_VALUES = ("pending", "approved", "rejected", "expired")


def upgrade() -> None:
    # Native PostgreSQL ENUM — owned by the table, dropped explicitly on
    # downgrade. ``create_type=False`` is NOT used here because the type
    # does not pre-exist ; alembic creates it as a side effect of the
    # column definition only when ``checkfirst`` is set, so we declare it
    # explicitly for clarity.
    db_write_approval_status = postgresql.ENUM(
        *_APPROVAL_STATUS_VALUES,
        name="db_write_approval_status",
        create_type=False,
    )
    db_write_approval_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "db_write_approvals",
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            db_write_approval_status,
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "touches_money_tables",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "llm_unavailable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("resume_url", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("submission_id"),
    )
    op.create_index(
        "idx_db_write_approvals_status_created",
        "db_write_approvals",
        ["status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_db_write_approvals_status_created")
    op.execute("DROP TABLE IF EXISTS db_write_approvals")
    op.execute("DROP TYPE IF EXISTS db_write_approval_status")
