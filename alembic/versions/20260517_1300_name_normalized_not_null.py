"""Add NOT NULL to products/stores.name_normalized generated columns.

Revision ID: 20260517_1300_namenorm_nn
Revises: 20260517_1200_oauth_only
Create Date: 2026-05-17 13:00:00.000000+00:00

Context (alembic check drift)
-----------------------------
``products.name_normalized`` and ``stores.name_normalized`` are STORED
generated columns added by migration ``20260430_1000_pipeline_v3_clean``::

    ALTER TABLE products ADD COLUMN name_normalized TEXT
    GENERATED ALWAYS AS (UPPER(immutable_unaccent(name))) STORED

That ``ALTER`` never declared ``NOT NULL``, so in PG the columns are
physically nullable (``pg_attribute.attnotnull = false``). The ORM models
(``ratis_core.models.product.Product`` /
``ratis_core.models.store.Store``) however declare them as
``Mapped[str] = mapped_column(..., nullable=False)`` — non-Optional, the
correct intent: ``name`` itself is ``NOT NULL`` so the generated
expression ``UPPER(immutable_unaccent(name))`` can never yield NULL.

``alembic check`` therefore reports a real ``modify_nullable`` drift on
both columns. This migration aligns PG with the model intent (the
semantically correct state) by adding the missing ``NOT NULL``.

A generated column can be marked ``SET NOT NULL`` like any other column;
because ``name`` is ``NOT NULL`` no existing row can violate the new
constraint, so the ``ALTER`` validates instantly without a backfill.
"""
from __future__ import annotations

from alembic import op


revision = "20260517_1300_namenorm_nn"
down_revision = "20260517_1200_oauth_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE products ALTER COLUMN name_normalized SET NOT NULL")
    op.execute("ALTER TABLE stores ALTER COLUMN name_normalized SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE stores ALTER COLUMN name_normalized DROP NOT NULL")
    op.execute("ALTER TABLE products ALTER COLUMN name_normalized DROP NOT NULL")
