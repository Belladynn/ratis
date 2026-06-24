"""add barcode_ean to match_method check

Revision ID: d14fe7c0cb81
Revises: 8ba48d7ec8bc
Create Date: 2026-04-16 15:05:16.354559+00:00

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd14fe7c0cb81'
down_revision: Union[str, None] = '8ba48d7ec8bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method")
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method "
        "CHECK (match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method")
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method "
        "CHECK (match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual'))"
    )
