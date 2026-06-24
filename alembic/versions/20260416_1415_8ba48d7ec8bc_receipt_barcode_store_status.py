"""receipt_barcode_store_status

Revision ID: 8ba48d7ec8bc
Revises: t4u5v6w7x8y9
Create Date: 2026-04-16 14:15:29.779408+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8ba48d7ec8bc'
down_revision: Union[str, None] = 't4u5v6w7x8y9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('receipts', sa.Column('receipt_barcode', sa.Text(), nullable=True))
    op.add_column('receipts', sa.Column('barcode_fields', sa.JSON(), nullable=True))
    op.add_column(
        'receipts',
        sa.Column('store_status', sa.Text(), server_default=sa.text("'confirmed'"), nullable=False),
    )
    op.create_index(
        'uq_receipts_receipt_barcode',
        'receipts',
        ['receipt_barcode'],
        unique=True,
        postgresql_where=sa.text('receipt_barcode IS NOT NULL'),
    )
    op.execute(
        "ALTER TABLE receipts ADD CONSTRAINT ck_receipts_store_status "
        "CHECK (store_status IN ('confirmed', 'pending', 'unknown'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_receipts_receipt_barcode")
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS ck_receipts_store_status")
    op.drop_column('receipts', 'store_status')
    op.drop_column('receipts', 'barcode_fields')
    op.drop_column('receipts', 'receipt_barcode')
