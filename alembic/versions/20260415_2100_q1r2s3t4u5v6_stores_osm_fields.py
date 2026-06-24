"""stores_osm_fields

Revision ID: q1r2s3t4u5v6
Revises: cfe77b80848d
Create Date: 2026-04-15 21:00:00.000000+00:00

Ajoute les colonnes OSM à stores :
  phone, siret, osm_id, store_code, opening_hours
Crée les index partiels UNIQUE sur phone, siret, osm_id.
Crée les index ordinaires sur brand et postal_code.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q1r2s3t4u5v6"
down_revision = "cfe77b80848d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column("stores", sa.Column("siret", sa.CHAR(14), nullable=True))
    op.add_column("stores", sa.Column("osm_id", sa.BigInteger(), nullable=True))
    op.add_column("stores", sa.Column("store_code", sa.Text(), nullable=True))
    op.add_column("stores", sa.Column("opening_hours", sa.Text(), nullable=True))

    # Partial unique indexes (NULL values excluded → no conflict between manual stores)
    op.execute(
        "CREATE UNIQUE INDEX uq_stores_phone  ON stores(phone)  WHERE phone  IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_stores_siret  ON stores(siret)  WHERE siret  IS NOT NULL"
    )
    op.create_index(
        "uq_stores_osm_id", "stores", ["osm_id"],
        unique=True,
        postgresql_where=sa.text("osm_id IS NOT NULL"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_stores_brand  ON stores(brand)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_stores_postal ON stores(postal_code)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_stores_postal")
    op.execute("DROP INDEX IF EXISTS ix_stores_brand")
    op.drop_index("uq_stores_osm_id", table_name="stores", if_exists=True)
    op.execute("DROP INDEX IF EXISTS uq_stores_siret")
    op.execute("DROP INDEX IF EXISTS uq_stores_phone")
    op.drop_column("stores", "opening_hours")
    op.drop_column("stores", "store_code")
    op.drop_column("stores", "osm_id")
    op.drop_column("stores", "siret")
    op.drop_column("stores", "phone")
