"""merge admin_audit + cross_retailer heads

Revision ID: 9082f271f4d5
Revises: 20260502_1900_admauad, 20260502_1900_xretail
Create Date: 2026-05-02 14:15:58.542281+00:00

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = '9082f271f4d5'
down_revision: Union[str, None] = ('20260502_1900_admauad', '20260502_1900_xretail')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
