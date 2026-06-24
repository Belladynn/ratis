"""Rename store brand → retailer (DA-33).

Revision ID: 20260421_2241_store_retailer
Revises: 20260421_2000_savings_snap
Create Date: 2026-04-21 22:41:00.000000+00:00

Disambiguate the "brand" term. Until now, two concepts collided :
- ``Brand`` table / ``products.brand_id`` → product manufacturer (Nestlé, Danone…)
- ``stores.brand`` TEXT column → retailer chain (Carrefour, Monoprix…)

This migration renames everything that relates to **retailer chain** to use the
``retailer`` terminology, leaving product-manufacturer columns untouched.

Renames performed :

- ``stores.brand`` → ``stores.retailer`` (+ index ``ix_stores_brand`` →
  ``ix_stores_retailer``, unique ``unique_store`` rebuilt on retailer, CHECK
  ``brand_not_empty`` → ``retailer_not_empty``).
- ``store_candidates.brand_guess`` → ``store_candidates.retailer_guess``.
- ``store_fingerprints`` signal_type CHECK values ``brand_postal`` →
  ``retailer_postal``, ``brand_postal_num`` → ``retailer_postal_num`` (data
  migrated, CHECK rebuilt).
- Table ``brand_receipt_formats`` → ``retailer_receipt_formats`` (with
  ``brand_key`` → ``retailer_key``, PK, trigger, tests updated).
"""
from __future__ import annotations

from alembic import op

revision = "20260421_2241_store_retailer"
down_revision = "20260421_2000_savings_snap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── stores ────────────────────────────────────────────────────────────────
    # Drop dependent indexes / check before rename (PostgreSQL will happily
    # rename columns with depending objects, but the index names reference the
    # old name and the functional unique index must be rebuilt).
    op.execute("ALTER TABLE stores DROP CONSTRAINT IF EXISTS brand_not_empty")
    op.execute("DROP INDEX IF EXISTS ix_stores_brand")
    op.execute("DROP INDEX IF EXISTS unique_store")

    op.alter_column("stores", "brand", new_column_name="retailer")

    op.execute(
        "ALTER TABLE stores ADD CONSTRAINT retailer_not_empty "
        "CHECK (retailer IS NULL OR retailer <> '')"
    )
    op.create_index("ix_stores_retailer", "stores", ["retailer"])
    op.execute(
        "CREATE UNIQUE INDEX unique_store ON stores "
        "(COALESCE(retailer, ''), COALESCE(address, ''), COALESCE(postal_code, ''))"
    )

    # ── store_candidates ──────────────────────────────────────────────────────
    op.alter_column(
        "store_candidates", "brand_guess", new_column_name="retailer_guess"
    )

    # ── store_fingerprints — data + CHECK rewrite ─────────────────────────────
    op.execute(
        "UPDATE store_fingerprints SET signal_type = 'retailer_postal' "
        "WHERE signal_type = 'brand_postal'"
    )
    op.execute(
        "UPDATE store_fingerprints SET signal_type = 'retailer_postal_num' "
        "WHERE signal_type = 'brand_postal_num'"
    )
    op.execute(
        "ALTER TABLE store_fingerprints "
        "DROP CONSTRAINT IF EXISTS ck_store_fingerprints_signal_type"
    )
    op.execute(
        "ALTER TABLE store_fingerprints ADD CONSTRAINT ck_store_fingerprints_signal_type "
        "CHECK (signal_type IN ('phone', 'store_code', 'barcode_prefix', "
        "'retailer_postal', 'retailer_postal_num'))"
    )

    # ── brand_receipt_formats → retailer_receipt_formats ──────────────────────
    op.execute(
        "DROP TRIGGER IF EXISTS trg_brand_receipt_formats_updated_at "
        "ON brand_receipt_formats"
    )
    op.execute(
        "ALTER TABLE brand_receipt_formats RENAME COLUMN brand_key TO retailer_key"
    )
    op.execute(
        "ALTER TABLE brand_receipt_formats "
        "RENAME CONSTRAINT brand_receipt_formats_pkey TO retailer_receipt_formats_pkey"
    )
    op.execute(
        "ALTER TABLE brand_receipt_formats RENAME TO retailer_receipt_formats"
    )
    op.execute(
        "CREATE TRIGGER trg_retailer_receipt_formats_updated_at "
        "BEFORE UPDATE ON retailer_receipt_formats "
        "FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()"
    )


def downgrade() -> None:
    # ── retailer_receipt_formats → brand_receipt_formats ──────────────────────
    op.execute(
        "DROP TRIGGER IF EXISTS trg_retailer_receipt_formats_updated_at "
        "ON retailer_receipt_formats"
    )
    op.execute(
        "ALTER TABLE retailer_receipt_formats RENAME TO brand_receipt_formats"
    )
    op.execute(
        "ALTER TABLE brand_receipt_formats "
        "RENAME CONSTRAINT retailer_receipt_formats_pkey TO brand_receipt_formats_pkey"
    )
    op.execute(
        "ALTER TABLE brand_receipt_formats RENAME COLUMN retailer_key TO brand_key"
    )
    op.execute(
        "CREATE TRIGGER trg_brand_receipt_formats_updated_at "
        "BEFORE UPDATE ON brand_receipt_formats "
        "FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()"
    )

    # ── store_fingerprints ────────────────────────────────────────────────────
    op.execute(
        "ALTER TABLE store_fingerprints "
        "DROP CONSTRAINT IF EXISTS ck_store_fingerprints_signal_type"
    )
    op.execute(
        "UPDATE store_fingerprints SET signal_type = 'brand_postal' "
        "WHERE signal_type = 'retailer_postal'"
    )
    op.execute(
        "UPDATE store_fingerprints SET signal_type = 'brand_postal_num' "
        "WHERE signal_type = 'retailer_postal_num'"
    )
    op.execute(
        "ALTER TABLE store_fingerprints ADD CONSTRAINT ck_store_fingerprints_signal_type "
        "CHECK (signal_type IN ('phone', 'store_code', 'barcode_prefix', "
        "'brand_postal', 'brand_postal_num'))"
    )

    # ── store_candidates ──────────────────────────────────────────────────────
    op.alter_column(
        "store_candidates", "retailer_guess", new_column_name="brand_guess"
    )

    # ── stores ────────────────────────────────────────────────────────────────
    op.execute("ALTER TABLE stores DROP CONSTRAINT IF EXISTS retailer_not_empty")
    op.execute("DROP INDEX IF EXISTS ix_stores_retailer")
    op.execute("DROP INDEX IF EXISTS unique_store")

    op.alter_column("stores", "retailer", new_column_name="brand")

    op.execute(
        "ALTER TABLE stores ADD CONSTRAINT brand_not_empty "
        "CHECK (brand IS NULL OR brand <> '')"
    )
    op.create_index("ix_stores_brand", "stores", ["brand"])
    op.execute(
        "CREATE UNIQUE INDEX unique_store ON stores "
        "(COALESCE(brand, ''), COALESCE(address, ''), COALESCE(postal_code, ''))"
    )
