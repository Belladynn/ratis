"""Add brand_receipt_formats table with seed (intermarche, monoprix).

Revision ID: d6e5f4a3b2c1
Revises: c5d4e3f2a1b0
Create Date: 2026-04-18 10:00:00.000000+00:00

Migre les formats de code-barres enseigne depuis ratis_settings.json vers une
table PostgreSQL scalable. Seed initial : intermarche et monoprix.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6e5f4a3b2c1"
down_revision = "c5d4e3f2a1b0"
branch_labels = None
depends_on = None

_SEED = [
    {
        "brand_key": "intermarche",
        "length": 24,
        "fields": [
            {"name": "date",       "start": 0,  "end": 8,  "format": "YYYYMMDD"},
            {"name": "time",       "start": 8,  "end": 12, "format": "HHMM"},
            {"name": "tx_id",      "start": 12, "end": 16},
            {"name": "caisse",     "start": 16, "end": 19},
            {"name": "store_code", "start": 19, "end": 24},
        ],
    },
    {
        "brand_key": "monoprix",
        "length": 24,
        "fields": [
            {"name": "store_code", "start": 0,  "end": 4},
            {"name": "caisse",     "start": 4,  "end": 7},
            {"name": "tx_id",      "start": 7,  "end": 12},
            {"name": "date",       "start": 12, "end": 18, "format": "YYMMDD"},
            {"name": "time",       "start": 18, "end": 24, "format": "HHMMSS"},
        ],
    },
]


def upgrade() -> None:
    op.create_table(
        "brand_receipt_formats",
        sa.Column("brand_key", sa.Text(), primary_key=True),
        sa.Column("length", sa.Integer(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.execute("""
        CREATE TRIGGER trg_brand_receipt_formats_updated_at
        BEFORE UPDATE ON brand_receipt_formats
        FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
    """)

    # Seed initial data — CAST(:fields AS jsonb) avoids psycopg VARCHAR inference
    for row in _SEED:
        op.execute(
            sa.text(
                "INSERT INTO brand_receipt_formats (brand_key, length, fields) "
                "VALUES (:brand_key, :length, CAST(:fields AS jsonb))"
            ).bindparams(
                brand_key=row["brand_key"],
                length=row["length"],
                fields=json.dumps(row["fields"]),
            )
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_brand_receipt_formats_updated_at ON brand_receipt_formats")
    op.execute("DROP TABLE IF EXISTS brand_receipt_formats")
