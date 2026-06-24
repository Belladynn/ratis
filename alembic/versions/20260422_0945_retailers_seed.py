"""Data migration : seed retailers FR + backfill stores.retailer_id (DA-34).

Revision ID: 20260422_0945_retailers_seed
Revises: 20260422_0930_retailers_norm
Create Date: 2026-04-22 09:45:00.000000+00:00

Structural migration ``20260422_0930_retailers_norm`` created the empty
tables + trigger. This data migration fills them :

1. Call ``ratis_core.seed.retailers.seed_retailers()`` to UPSERT the FR
   retailer dictionary from ``ratis_core/config/retailers_fr.json``.
2. Backfill ``stores.retailer_id`` by resolving
   ``lower(trim(stores.retailer))`` against ``retailer_aliases.alias``
   (case-insensitive). Stores with no match keep ``retailer_id = NULL``
   and their TEXT cache — the next ``batch_osm_sync`` run will re-resolve
   them (and auto-create an unverified retailer if needed).

Downgrade is a no-op on data : we do NOT delete seeded rows, because other
tables (retailer_aliases via CASCADE would be fine, but future writes on
stores.retailer_id would be orphaned anyway once the structural migration is
reverted).
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "20260422_0945_retailers_seed"
down_revision = "20260422_0930_retailers_norm"
branch_labels = None
depends_on = None

_log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    # Use a Session wrapping the migration connection so seed_retailers gets a
    # real SQLAlchemy Session interface (it passes text() and .execute()).
    session = Session(bind=bind)
    try:
        from ratis_core.seed.retailers import seed_retailers

        stats = seed_retailers(session)
        _log.info(
            "retailers seeded : inserted=%d updated=%d aliases_added=%d",
            stats["inserted"],
            stats["updated"],
            stats["aliases_added"],
        )

        # Backfill stores.retailer_id by alias lookup. ``lower(trim(...))`` on
        # both sides. We update via a single UPDATE … FROM so only stores with
        # a match are touched. The trigger ``trg_stores_sync_retailer_text``
        # will re-sync stores.retailer from retailers.canonical_name.
        result = bind.execute(
            text(
                """
                UPDATE stores
                SET retailer_id = ra.retailer_id
                FROM retailer_aliases ra
                WHERE stores.retailer_id IS NULL
                  AND stores.retailer IS NOT NULL
                  AND ra.alias = lower(trim(stores.retailer))
                """
            )
        )
        _log.info("backfilled stores.retailer_id on %d rows", result.rowcount)
    finally:
        # Session shares the migration transaction — do not commit from here.
        session.close()


def downgrade() -> None:
    bind = op.get_bind()
    # Clear the FK on stores before the structural migration drops it; leaves
    # the text cache intact.
    bind.execute(text("UPDATE stores SET retailer_id = NULL"))
    # Truncate aliases + retailers so a fresh re-upgrade starts clean.
    # (retailer_aliases cascades on retailers.id delete; wipe both.)
    bind.execute(text("TRUNCATE retailer_aliases"))
    bind.execute(text("DELETE FROM retailers"))
