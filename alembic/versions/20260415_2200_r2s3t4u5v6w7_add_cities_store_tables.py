"""add_cities_store_tables

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-04-15 22:00:00.000000+00:00

Crée :
  - cities (postal_code, city_name, department, country_code)
  - store_fingerprints (signal_type, signal_value → store_id)
  - store_candidates (stores inconnus en attente de validation)
Active pg_trgm (pour la similarité fuzzy adresse, Plan 2).
Crée les triggers updated_at pour store_fingerprints et store_candidates.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pg_trgm : déjà activé dans 20260406 mais idempotent
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── cities ────────────────────────────────────────────────────────────────
    op.create_table(
        "cities",
        sa.Column("postal_code", sa.Text(), nullable=False),
        sa.Column("city_name", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("country_code", sa.Text(), nullable=False, server_default="FR"),
        sa.PrimaryKeyConstraint("postal_code", "city_name", name="pk_cities"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cities_postal ON cities(postal_code)")

    # ── store_fingerprints ────────────────────────────────────────────────────
    op.create_table(
        "store_fingerprints",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_type", sa.Text(), nullable=False),
        sa.Column("signal_value", sa.Text(), nullable=False),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], ondelete="CASCADE",
            name="fk_store_fingerprints_store_id",
        ),
        sa.UniqueConstraint(
            "signal_type", "signal_value", name="uq_store_fingerprints_signal"
        ),
        sa.CheckConstraint(
            "signal_type IN ('phone', 'store_code', 'barcode_prefix', 'brand_postal', 'brand_postal_num')",
            name="ck_store_fingerprints_signal_type",
        ),
    )
    op.execute("""
        CREATE TRIGGER trg_store_fingerprints_updated_at
        BEFORE UPDATE ON store_fingerprints
        FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
    """)

    # ── store_candidates ──────────────────────────────────────────────────────
    op.create_table(
        "store_candidates",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("raw_header", sa.Text(), nullable=False),
        sa.Column("brand_guess", sa.Text(), nullable=True),
        sa.Column("address_guess", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("matched_store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["matched_store_id"], ["stores.id"], ondelete="SET NULL",
            name="fk_store_candidates_matched_store_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'matched', 'ignored')",
            name="ck_store_candidates_status",
        ),
    )
    op.execute("""
        CREATE TRIGGER trg_store_candidates_updated_at
        BEFORE UPDATE ON store_candidates
        FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_store_candidates_updated_at ON store_candidates")
    op.drop_table("store_candidates")
    op.execute("DROP TRIGGER IF EXISTS trg_store_fingerprints_updated_at ON store_fingerprints")
    op.drop_table("store_fingerprints")
    op.execute("DROP INDEX IF EXISTS ix_cities_postal")
    op.drop_table("cities")
