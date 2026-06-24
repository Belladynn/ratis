"""receipts_store_id_nullable

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-04-15 24:00:00.000000+00:00

Rend receipts.store_id nullable pour permettre les scans sans store connu.
La détection OCR tentera de renseigner ce champ après réception du ticket.
Change aussi la FK de RESTRICT → SET NULL (store supprimé = store_id mis à NULL).
"""
from __future__ import annotations

from alembic import op


revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("receipts", "store_id", nullable=True)
    # FK: RESTRICT → SET NULL so store deletion nullifies store_id rather than blocking
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS fk_store")
    op.create_foreign_key(
        "fk_store", "receipts", "stores", ["store_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS fk_store")
    op.create_foreign_key(
        "fk_store", "receipts", "stores", ["store_id"], ["id"], ondelete="RESTRICT"
    )
    op.execute("DELETE FROM receipts WHERE store_id IS NULL")
    op.alter_column("receipts", "store_id", nullable=False)
