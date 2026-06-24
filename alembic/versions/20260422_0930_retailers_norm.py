"""Retailers normalization : retailers + retailer_aliases + stores.retailer_id + ocr_knowledge.entity_id (DA-34).

Revision ID: 20260422_0930_retailers_norm
Revises: 20260422_0925_retailer_header
Create Date: 2026-04-22 09:30:00.000000+00:00

Design C :

- New table ``retailers`` : normalized chain / sub-brand dictionary, self-ref
  parent hierarchy (Carrefour Market → Carrefour). Indexed by slug.
- New table ``retailer_aliases`` : lowercased strings → retailer_id. Hot path
  for OSM sync + receipt-header OCR resolution. Source ∈ {osm, receipt_header,
  manual}.
- ``stores.retailer_id`` FK → retailers(id) ON DELETE SET NULL. The legacy
  ``stores.retailer`` TEXT column stays as a denormalized cache kept in sync
  by a PostgreSQL trigger (readers & existing queries unchanged).
- ``ocr_knowledge.entity_id`` UUID nullable : polymorphic resolved-entity id
  (type determines target table). No formal FK — cross-table polymorphism.
- Triggers :
    * fn_sync_store_retailer_text() BEFORE INSERT/UPDATE OF retailer_id ON stores
      → keeps stores.retailer = retailers.canonical_name.
    * fn_cascade_retailer_canonical_name_change() AFTER UPDATE OF canonical_name
      ON retailers → propagates to stores.retailer.

Seeding and backfill are handled by the follow-up data migration
(20260422_0945_retailers_seed), so this migration is purely structural.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260422_0930_retailers_norm"
down_revision = "20260422_0925_retailer_header"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── retailers ─────────────────────────────────────────────────────────────
    op.create_table(
        "retailers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("retailers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("color_hex", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column(
            "country_code",
            sa.CHAR(2),
            nullable=False,
            server_default=sa.text("'FR'"),
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
        sa.CheckConstraint(
            r"color_hex IS NULL OR color_hex ~ '^#[0-9A-Fa-f]{6}$'",
            name="ck_retailers_color_hex",
        ),
    )
    op.create_index("idx_retailers_slug", "retailers", ["slug"])
    op.create_index("idx_retailers_parent", "retailers", ["parent_id"])

    # updated_at trigger (reuses global fn_set_updated_at from initial schema).
    op.execute(
        """
        CREATE OR REPLACE TRIGGER trg_retailers_updated_at
        BEFORE UPDATE ON retailers
        FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
        """
    )

    # ── retailer_aliases ──────────────────────────────────────────────────────
    op.create_table(
        "retailer_aliases",
        sa.Column(
            "retailer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("retailers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("alias", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source IN ('osm', 'receipt_header', 'manual')",
            name="ck_retailer_aliases_source",
        ),
    )
    op.create_index(
        "idx_retailer_aliases_alias", "retailer_aliases", ["alias"]
    )

    # ── stores.retailer_id ────────────────────────────────────────────────────
    op.add_column(
        "stores",
        sa.Column(
            "retailer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("retailers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("idx_stores_retailer_id", "stores", ["retailer_id"])

    # ── ocr_knowledge.entity_id ───────────────────────────────────────────────
    op.add_column(
        "ocr_knowledge",
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "idx_ocr_knowledge_entity_id", "ocr_knowledge", ["entity_id"]
    )

    # ── triggers : denormalized cache sync ────────────────────────────────────
    # Forward sync: stores.retailer_id change → update stores.retailer text.
    # When retailer_id is NULL we leave the TEXT column unchanged — it may be
    # an unresolved OCR'd header that will be re-resolved later (Part B,
    # batch_osm_sync). Legacy writers that still set the TEXT column directly
    # keep working; normalized writers set retailer_id and get the cache for
    # free.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_sync_store_retailer_text()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.retailer_id IS NOT NULL THEN
                NEW.retailer := (
                    SELECT canonical_name
                    FROM retailers
                    WHERE id = NEW.retailer_id
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stores_sync_retailer_text
        BEFORE INSERT OR UPDATE OF retailer_id ON stores
        FOR EACH ROW EXECUTE FUNCTION fn_sync_store_retailer_text();
        """
    )

    # Backward sync: retailers.canonical_name change → propagate to stores.retailer.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_cascade_retailer_canonical_name_change()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.canonical_name IS DISTINCT FROM OLD.canonical_name THEN
                UPDATE stores
                SET retailer = NEW.canonical_name
                WHERE retailer_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_retailers_cascade_name_change
        AFTER UPDATE OF canonical_name ON retailers
        FOR EACH ROW EXECUTE FUNCTION fn_cascade_retailer_canonical_name_change();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_retailers_cascade_name_change ON retailers"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS fn_cascade_retailer_canonical_name_change()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_stores_sync_retailer_text ON stores"
    )
    op.execute("DROP FUNCTION IF EXISTS fn_sync_store_retailer_text()")

    op.execute("DROP INDEX IF EXISTS idx_ocr_knowledge_entity_id")
    op.drop_column("ocr_knowledge", "entity_id")

    op.execute("DROP INDEX IF EXISTS idx_stores_retailer_id")
    op.drop_column("stores", "retailer_id")

    op.execute("DROP INDEX IF EXISTS idx_retailer_aliases_alias")
    op.drop_table("retailer_aliases")

    op.execute("DROP TRIGGER IF EXISTS trg_retailers_updated_at ON retailers")
    op.execute("DROP INDEX IF EXISTS idx_retailers_parent")
    op.execute("DROP INDEX IF EXISTS idx_retailers_slug")
    op.drop_table("retailers")
