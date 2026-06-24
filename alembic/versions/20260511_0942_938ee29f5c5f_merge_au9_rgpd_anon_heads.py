"""merge au9 + rgpd anon heads

Revision ID: 938ee29f5c5f
Revises: 20260511_1000_au9npfk, 20260511_1000_rgpd_anon
Create Date: 2026-05-11 09:42:12.487281+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '938ee29f5c5f'
down_revision: Union[str, None] = ('20260511_1000_au9npfk', '20260511_1000_rgpd_anon')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
