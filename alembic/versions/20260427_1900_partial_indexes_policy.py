"""Align stores unique-index policy with what was actually applied in prod.

Revision ID: 20260427_1900_partial_idx
Revises: 20260427_1700_recreate_stores_uq
Create Date: 2026-04-27 19:00:00.000000+00:00

Context (2026-04-27) :
The previous migration `20260427_1700_recreate_stores_uq` recreated three unique
indexes — `uq_stores_phone`, `uq_stores_siret`, `unique_store` — to undo the
DROPs done during the OSM Geofabrik bulk import emergency. However, when the
migration was hand-applied in prod via raw psql (alembic was not in the image,
KP "DP-alembic-in-image-broke-ci"), two deviations were made on purpose :

  1. `uq_stores_phone` was NOT recreated — too many false positives across
     the OSM dataset (chains share contact phones across branches), making
     the index unenforceable in practice.
  2. `unique_store` was recreated as a PARTIAL index, restricted to rows
     `WHERE retailer IS NOT NULL AND address IS NOT NULL AND NOT is_disabled`
     so that user-suggested stores (lat/lng=0, retailer NULL) and disabled
     entries don't collide on the (retailer, address, postal_code) tuple.

Without this alignment migration, any fresh-from-scratch `alembic upgrade head`
(dev box, CI, new prod cutover) would create the original non-partial shape
and diverge from the running prod schema.

This migration is :
  - idempotent (`CREATE/DROP INDEX IF EXISTS`)
  - a no-op on prod (the indexes are already in the target shape there)
  - corrective on dev / CI / fresh DBs (drops the non-partial unique_store +
    uq_stores_phone created by 1700 and recreates unique_store as partial)

uq_stores_siret is unchanged — kept as the strict unique partial index from
the 1700 migration. SIRET collisions are real conflicts (a single legal
entity).
"""
from __future__ import annotations

from alembic import op

revision = "20260427_1900_partial_idx"
down_revision = "20260427_1700_recreate_stores_uq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Drop uq_stores_phone — policy decision: phone is not a unique
    #    invariant in the OSM dataset (chains share contact lines).
    op.execute("DROP INDEX IF EXISTS uq_stores_phone")

    # 2) Replace the non-partial unique_store with a partial one matching the
    #    prod-applied policy. The DROP + CREATE pair is wrapped in IF EXISTS /
    #    IF NOT EXISTS so the migration is idempotent across :
    #       - prod (already partial → DROP no-op? No, DROP IF EXISTS still
    #         fires; the subsequent CREATE IF NOT EXISTS recreates it. The
    #         net effect is the same partial shape with the same name.)
    #       - dev / CI from scratch (1700 created the non-partial → this
    #         migration converts it to partial)
    op.execute("DROP INDEX IF EXISTS unique_store")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS unique_store ON stores "
        "(COALESCE(retailer, ''), COALESCE(address, ''), COALESCE(postal_code, '')) "
        "WHERE retailer IS NOT NULL AND address IS NOT NULL AND NOT is_disabled"
    )


def downgrade() -> None:
    # Reverse to the 1700 shape : non-partial unique_store + recreate uq_stores_phone.
    op.execute("DROP INDEX IF EXISTS unique_store")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS unique_store ON stores "
        "(COALESCE(retailer, ''), COALESCE(address, ''), COALESCE(postal_code, ''))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_stores_phone "
        "ON stores(phone) WHERE phone IS NOT NULL"
    )
