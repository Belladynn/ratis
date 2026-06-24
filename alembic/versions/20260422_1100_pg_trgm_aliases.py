"""pg_trgm GIN index on retailer_aliases.alias (DA-35).

Revision ID: 20260422_1100_pg_trgm_aliases
Revises: 20260422_0945_retailers_seed
Create Date: 2026-04-22 11:00:00.000000+00:00

Fuzzy retailer-header matching from receipt OCR needs a trigram index on
``retailer_aliases.alias`` so similarity() queries stay sub-millisecond.

``pg_trgm`` itself is already enabled by earlier migrations (see
``20260406_1000_...add_pg_trgm_index_products_name``) but we use
``CREATE EXTENSION IF NOT EXISTS`` defensively here too — the index is
what's new.

Downgrade only drops the index. The extension is left in place because
several other indexes (e.g. ``gin_products_name``) depend on it and
cross-migration extension drops are never safe.
"""
from __future__ import annotations

from alembic import op

revision = "20260422_1100_pg_trgm_aliases"
down_revision = "20260422_0945_retailers_seed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_retailer_aliases_alias_trgm "
        "ON retailer_aliases USING gin (alias gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_retailer_aliases_alias_trgm")
