"""postgis geo layer — extension, stores.geog generated column, GIST index

Revision ID: 20260515_1200_postgisgeo
Revises: 20260513_1000_btxttrgm
"""
from alembic import op

revision = "20260515_1200_postgisgeo"
down_revision = "20260513_1000_btxttrgm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(
        "ALTER TABLE stores ADD COLUMN geog geography(Point, 4326) "
        "GENERATED ALWAYS AS ("
        "CASE WHEN lat = 0 AND lng = 0 THEN NULL "
        "ELSE ST_SetSRID("
        "ST_MakePoint(lng::double precision, lat::double precision), 4326"
        ")::geography END"
        ") STORED"
    )
    op.execute("CREATE INDEX ix_stores_geog ON stores USING GIST (geog)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_stores_geog")
    op.execute("ALTER TABLE stores DROP COLUMN IF EXISTS geog")
    # L'extension PostGIS est laissée en place volontairement.
