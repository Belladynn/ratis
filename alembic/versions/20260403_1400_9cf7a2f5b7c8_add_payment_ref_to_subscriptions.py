"""add payment_ref to subscriptions

Revision ID: 9cf7a2f5b7c8
Revises: a24771cd61a7
Create Date: 2026-04-03 14:00:25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9cf7a2f5b7c8'
down_revision: Union[str, None] = 'a24771cd61a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('payment_ref', sa.Text(), nullable=True))
    op.create_check_constraint(
        'payment_ref_coherence',
        'subscriptions',
        "paid_with = 'cashback' OR payment_ref IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint('payment_ref_coherence', 'subscriptions', type_='check')
    op.drop_column('subscriptions', 'payment_ref')
