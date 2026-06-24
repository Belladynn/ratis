"""product_contributions table (Phase C-5).

Revision ID: 20260511_2100_c5pc
Revises: 20260511_2300_c1org
Create Date: 2026-05-11 21:00:00

Phase C-5 of the missions sprint lands the user-facing endpoint
``POST /api/v1/product/{ean}/contribute`` letting users complete
missing fields on a product (brands / categories_tags / labels_tags /
name). Each accepted contribution emits a ``trigger_action(
"fill_product_field", qualifier=None, ...)`` which drives the 6
None-qualifier ``fill_product_field`` mission templates already
``is_active=true`` in the catalogue (seeded by ``miss_pb``).

This migration creates the audit / forensics table that backs the
endpoint :

  * ``status='applied'`` rows : the products row was updated in place
    because the target field was NULL/empty. Mission credit fired.
  * ``status='pending_review'`` rows : the field already had a value,
    the contribution is parked for admin review. No mission credit.
  * ``status='rejected'`` rows : admin marked the contribution as
    invalid (out of scope C-5, admin endpoints follow in a separate
    PR).

Value shape : ``brands`` / ``name`` are scalars → ``value_text`` ;
``categories_tags`` / ``labels_tags`` are OFF tag arrays → ``value_array``.
The ``ck_contributions_value_shape`` CHECK enforces exactly one of the
two columns is populated, matching the declared field family.

User-id is ``ON DELETE SET NULL`` because the contribution stays
useful for the catalogue audit trail even after the contributor
account is hard-deleted (RGPD anonymization preserves the data,
just severs the link).

Idempotent : the table is created with ``IF NOT EXISTS`` semantics
implicit in Alembic's ``create_table`` — a re-run after partial
failure stops on the first re-create with a clear error, which is
the desired behaviour.

Down-migration : drops the table + indexes (no row preservation —
this is V0 and no production data depends on the table yet).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260511_2100_c5pc"
down_revision = "20260511_2300_c1org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_contributions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_ean", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_array", ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'applied'"),
        ),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_admin_id", UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "field IN ('brands', 'categories_tags', 'labels_tags', 'name')",
            name="ck_contributions_field",
        ),
        sa.CheckConstraint(
            # Scalar fields (brands / name) require value_text ; array
            # fields (categories_tags / labels_tags) require value_array.
            "("
            "    field IN ('brands', 'name')"
            "    AND value_text IS NOT NULL"
            "    AND value_array IS NULL"
            ") OR ("
            "    field IN ('categories_tags', 'labels_tags')"
            "    AND value_array IS NOT NULL"
            "    AND value_text IS NULL"
            ")",
            name="ck_contributions_value_shape",
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'rejected', 'pending_review')",
            name="ck_contributions_status",
        ),
    )

    op.create_index(
        "idx_product_contributions_user_ean",
        "product_contributions",
        ["user_id", "product_ean"],
    )

    # Partial index — only the pending_review queue needs ordered
    # scanning (admin triage). Other statuses are read by id.
    op.create_index(
        "idx_product_contributions_status_created",
        "product_contributions",
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'pending_review'"),
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS idx_product_contributions_status_created"
    )
    op.execute("DROP INDEX IF EXISTS idx_product_contributions_user_ean")
    op.execute("DROP TABLE IF EXISTS product_contributions")
