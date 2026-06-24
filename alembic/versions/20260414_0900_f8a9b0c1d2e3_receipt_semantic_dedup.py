"""receipt semantic dedup — purchased_at_with_time + unique key

Revision ID: f8a9b0c1d2e3
Revises: e5f6a7b8c9d0
Create Date: 2026-04-14 09:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP

revision = "f8a9b0c1d2e3"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TIMESTAMP(0) = precision à la seconde, sans timezone (heure locale du ticket).
    # nullable — l'OCR ne peut pas toujours extraire l'heure.
    op.add_column(
        "receipts",
        sa.Column(
            "purchased_at_with_time",
            PG_TIMESTAMP(precision=0, timezone=False),
            nullable=True,
        ),
    )
    # Clé sémantique de déduplication cross-user.
    # Partielle : WHERE NOT NULL sur les deux colonnes — un ticket sans heure OCR
    # ne doit pas bloquer d'autres tickets du même magasin au même montant.
    op.create_index(
        "receipts_semantic_dedup_key",
        "receipts",
        ["store_id", "purchased_at_with_time", "total_amount"],
        unique=True,
        postgresql_where=sa.text(
            "purchased_at_with_time IS NOT NULL AND total_amount IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("receipts_semantic_dedup_key", table_name="receipts")
    op.drop_column("receipts", "purchased_at_with_time")
