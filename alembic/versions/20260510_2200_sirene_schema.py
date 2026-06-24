"""extend stores.source CHECK + ix_stores_siret_lookup + sirene_geocode_cache

Revision ID: 20260510_2200_sirene_schema
Revises: 20260510_2100_pfd
Create Date: 2026-05-10 22:00:00.000000

SIRENE PR1 — schema foundations for the SIRENE/multi-source store ingestion
plan (cf. ``docs/superpowers/plans/2026-05-10-sirene-impl.md`` § PR1).

Schema additions
----------------
1. Extend ``stores.source`` CHECK constraint to allow two new values :
     ``sirene``  : INSEE-SIRENE batch (FR primary, monthly).
     ``overture``: Overture Maps (international fallback, V3 anticipation).
   The drop+recreate pattern is used (single CHECK constraint per table is
   the cleanest way to evolve an enum-like text column without resorting to
   a real ENUM type — which Ratis avoids per migration tradition).

2. Partial index ``ix_stores_siret_lookup`` on ``stores(siret) WHERE siret
   IS NOT NULL`` — the SIRENE batch upserts O(M) rows by SIRET each month,
   and most existing rows (OSM/admin/user_suggested) have NULL siret. A
   partial index keeps the on-disk size tiny.

   *Redundancy note* : the existing ``uq_stores_siret`` UNIQUE partial
   index (declared via raw SQL in migration ``20260415_2100_*``) already
   covers point-lookup queries on SIRET. We still add the new
   ``ix_stores_siret_lookup`` because (a) it makes the lookup contract
   visible at the ORM layer (``Store.__table_args__``) where future
   SAs read first, (b) the on-disk cost is negligible (NULL siret on
   most rows + partial WHERE = tiny index), (c) the plan explicitly
   names this index for PR2 ``find_match()`` to use. If we ever
   migrate ``uq_stores_siret`` into the ORM declarative layer, this
   index can be dropped.

3. New table ``sirene_geocode_cache`` (PK ``siret CHAR(14)``) memoising
   Géoplateforme bulk-geocoding answers so the monthly batch does not
   re-geocode unchanged addresses. ``address_hash`` lets the batch detect
   address changes and re-geocode only those rows. Failed geocodes are
   still cached with NULL coordinates to avoid retrying dead addresses.

Backfill / data risk (KP-42 audit)
----------------------------------
None. This migration is purely additive — no UPDATE on existing rows.
The CHECK constraint is *widened* (every value previously accepted is
still accepted), so no existing row can violate it.

CONCURRENTLY note
-----------------
``CREATE INDEX CONCURRENTLY`` is intentionally *not* used : Alembic
migrations in this repo run inside a single transaction (no precedent of
concurrent index creation). The ``stores`` table is small enough on dev
and prod-V1 that a regular ``CREATE INDEX`` blocks for milliseconds.

Downgrade
---------
Reverse order : drop the cache table → drop the new index → restore the
old CHECK constraint with the original 3-value enum.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers (≤32 chars per R-DB-08).
revision = "20260510_2200_sirene_schema"
down_revision = "20260510_2100_pfd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend the stores.source CHECK enum.
    # IF EXISTS guard per R-DB-07 — the original constraint comes from
    # migration b3c4d5e6f7a8 (2026-04-18).
    op.execute("ALTER TABLE stores DROP CONSTRAINT IF EXISTS ck_stores_source")
    op.create_check_constraint(
        "ck_stores_source",
        "stores",
        "source IN ('osm', 'sirene', 'overture', 'admin', 'user_suggested')",
    )

    # 2. Partial index on stores.siret for SIRENE upsert lookups.
    # Most rows (OSM/admin/user_suggested) keep NULL siret → partial WHERE
    # keeps the index small.
    op.create_index(
        "ix_stores_siret_lookup",
        "stores",
        ["siret"],
        postgresql_where=sa.text("siret IS NOT NULL"),
    )

    # 3. Geocoding cache table — keyed by SIRET (PK), with an
    # address_hash secondary index so the batch can detect address
    # changes via "WHERE address_hash != :new_hash".
    op.create_table(
        "sirene_geocode_cache",
        sa.Column("siret", sa.CHAR(14), primary_key=True),
        sa.Column("address_hash", sa.Text(), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("score", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "geocoded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_sirene_geocode_cache_address_hash",
        "sirene_geocode_cache",
        ["address_hash"],
    )


def downgrade() -> None:
    # Reverse order : cache table → siret index → restore old CHECK enum.
    op.execute("DROP INDEX IF EXISTS ix_sirene_geocode_cache_address_hash")
    op.drop_table("sirene_geocode_cache")

    op.execute("DROP INDEX IF EXISTS ix_stores_siret_lookup")

    op.execute("ALTER TABLE stores DROP CONSTRAINT IF EXISTS ck_stores_source")
    op.create_check_constraint(
        "ck_stores_source",
        "stores",
        "source IN ('osm', 'admin', 'user_suggested')",
    )
