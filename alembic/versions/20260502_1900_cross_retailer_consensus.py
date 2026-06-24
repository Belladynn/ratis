"""Cross-retailer consensus schema (bloc A).

Revision ID: 20260502_1900_xretail
Revises: 20260502_1700_consmatch
Create Date: 2026-05-02 19:00:00

Foundation for the cross-retailer consensus refactor — see
``webservices/ratis_product_analyser/ARCH_cross_retailer_consensus.md``.

Bloc A scope :

- ``product_name_resolutions.source_type`` TEXT NOT NULL DEFAULT 'receipt'
  CHECK ('receipt' | 'esl') — separates ticket-derived rows from ESL
  (electronic shelf label) rows. Existing rows are receipts by construction.
- ``product_name_resolutions.retailer_id`` UUID NULL FK retailers
  ON DELETE RESTRICT — denormalized retailer key. Filled by the trigger
  ``fn_sync_pnr_retailer_id`` from ``stores.retailer_id`` at INSERT (and
  on UPDATE OF store_id). NULL-tolerant : rows from stores without a
  resolved retailer are kept but excluded from the consensus path via
  partial indexes ``WHERE retailer_id IS NOT NULL``.
- CHECK ``pnr_match_method_check`` extended additively with ``'esl'`` and
  ``'cross_source_esl_exact'`` (DROP IF EXISTS + recreate, R-mig-drop).
- UNIQUE INDEX migrated : ``idx_pnr_scan_label`` (scan_id, normalized_label)
  → ``idx_pnr_scan_source_label`` (scan_id, source_type, normalized_label),
  so a single scan may hold one receipt + one ESL row for the same label.
- New consensus path indexes (partial WHERE retailer_id IS NOT NULL) :
  * ``idx_pnr_retailer_source_label`` btree on (retailer_id, source_type,
    normalized_label) — exact lookup hot path for the matcher cascade.
  * ``idx_pnr_norm_label_trgm`` GIN with ``gin_trgm_ops`` on
    ``normalized_label`` — fuzzy retailer-wide consensus search.
- Trigger ``fn_sync_pnr_retailer_id`` BEFORE INSERT OR UPDATE OF store_id
  — denorm ``retailer_id`` from ``stores`` so the application code never
  needs to write it directly.
- Backfill : UPDATE existing alpha rows to populate retailer_id from
  the join on stores. Strictly additive — only runs where retailer_id
  IS NULL. Trivial in production (the ledger is empty at merge time).

Down-migration restores the previous schema exactly : drops the new
column / constraint additions, recreates the original UNIQUE index, and
removes trigger + function. ``pg_trgm`` extension is left in place
(shared by other indexes — see ``20260428_1000_brands_trgm`` and
``20260406_1000_e4a7c2d8f901_add_pg_trgm_index_products_name``).

Defensive patterns (R-mig-drop / R07) :
- ``DROP CONSTRAINT IF EXISTS`` for CHECK swap
- ``DROP INDEX IF EXISTS`` before recreating
- ``CREATE EXTENSION IF NOT EXISTS pg_trgm`` (no-op if present)
- ``DROP TRIGGER IF EXISTS`` + ``CREATE OR REPLACE FUNCTION``
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260502_1900_xretail"
down_revision = "20260502_1700_consmatch"
branch_labels = None
depends_on = None


_NEW_MATCH_METHOD = (
    "match_method IN ("
    "'barcode', 'manual_admin', 'fuzzy_pending', 'observed_name', "
    "'esl', 'cross_source_esl_exact'"
    ")"
)
_OLD_MATCH_METHOD = (
    "match_method IN ("
    "'barcode', 'manual_admin', 'fuzzy_pending', 'observed_name'"
    ")"
)


def upgrade() -> None:
    # pg_trgm required for the GIN trgm index. Idempotent (already present
    # via earlier migrations on prod ; this is a safety net for fresh DBs).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── New columns ──────────────────────────────────────────────────────────
    # source_type : NOT NULL DEFAULT 'receipt' covers existing rows (which
    # are by construction ticket-derived). DEFAULT removed afterwards is
    # not strictly necessary — keeping it allows existing INSERT call-sites
    # to omit the column (records always default to 'receipt' for safety).
    op.add_column(
        "product_name_resolutions",
        sa.Column(
            "source_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'receipt'"),
        ),
    )
    op.create_check_constraint(
        "pnr_source_type_check",
        "product_name_resolutions",
        "source_type IN ('receipt', 'esl')",
    )
    # retailer_id : NULL-tolerant. Populated by the trigger on INSERT and
    # UPDATE OF store_id ; backfilled below for legacy rows.
    op.add_column(
        "product_name_resolutions",
        sa.Column(
            "retailer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "retailers.id",
                ondelete="RESTRICT",
                name="product_name_resolutions_retailer_id_fkey",
            ),
            nullable=True,
        ),
    )

    # ── CHECK match_method extended additively ───────────────────────────────
    op.execute(
        "ALTER TABLE product_name_resolutions "
        "DROP CONSTRAINT IF EXISTS pnr_match_method_check"
    )
    op.execute(
        f"ALTER TABLE product_name_resolutions "
        f"ADD CONSTRAINT pnr_match_method_check CHECK ({_NEW_MATCH_METHOD})"
    )

    # ── UNIQUE index migration ───────────────────────────────────────────────
    # Old : (scan_id, normalized_label) ; new : (scan_id, source_type,
    # normalized_label). One row per (scan, source, label) — receipts and
    # ESLs no longer collide for the same scan.
    op.execute("DROP INDEX IF EXISTS idx_pnr_scan_label")
    op.create_index(
        "idx_pnr_scan_source_label",
        "product_name_resolutions",
        ["scan_id", "source_type", "normalized_label"],
        unique=True,
    )

    # ── New consensus-path indexes (partial : retailer_id IS NOT NULL) ───────
    # Exact lookup btree — used by ``get_consensus_for_label`` (bloc B).
    op.create_index(
        "idx_pnr_retailer_source_label",
        "product_name_resolutions",
        ["retailer_id", "source_type", "normalized_label"],
        postgresql_where=sa.text("retailer_id IS NOT NULL"),
    )
    # Fuzzy retailer-wide search — used by ``find_fuzzy_verified_consensus``
    # (bloc B). GIN gin_trgm_ops accelerates similarity / ILIKE substring.
    op.execute(
        "CREATE INDEX idx_pnr_norm_label_trgm "
        "ON product_name_resolutions "
        "USING GIN (normalized_label gin_trgm_ops) "
        "WHERE retailer_id IS NOT NULL"
    )

    # ── Trigger : denorm retailer_id from stores ─────────────────────────────
    # BEFORE INSERT and BEFORE UPDATE OF store_id — keeps retailer_id in
    # sync without touching application code. The IF NEW.retailer_id IS NULL
    # guard intentionally lets explicit application writes win (defensive
    # edge case — current code never writes retailer_id directly, but if
    # a future code path does, the trigger does not stomp it).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_sync_pnr_retailer_id()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.store_id IS NOT NULL AND NEW.retailer_id IS NULL THEN
                NEW.retailer_id := (
                    SELECT retailer_id FROM stores WHERE id = NEW.store_id
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_pnr_sync_retailer_id "
        "ON product_name_resolutions"
    )
    op.execute(
        """
        CREATE TRIGGER trg_pnr_sync_retailer_id
        BEFORE INSERT OR UPDATE OF store_id ON product_name_resolutions
        FOR EACH ROW EXECUTE FUNCTION fn_sync_pnr_retailer_id();
        """
    )

    # ── Backfill retailer_id on legacy rows ──────────────────────────────────
    # Strictly additive : only updates rows where retailer_id IS NULL and
    # store_id resolves to a retailer. KP-42 audit : the condition is
    # narrow ; the production ledger is empty at merge time so this is
    # effectively a no-op there. Alpha rows benefit immediately.
    op.execute(
        """
        UPDATE product_name_resolutions pnr
        SET retailer_id = s.retailer_id
        FROM stores s
        WHERE pnr.store_id = s.id
          AND pnr.retailer_id IS NULL
          AND s.retailer_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_pnr_sync_retailer_id "
        "ON product_name_resolutions"
    )
    op.execute("DROP FUNCTION IF EXISTS fn_sync_pnr_retailer_id()")

    op.execute("DROP INDEX IF EXISTS idx_pnr_norm_label_trgm")
    op.execute("DROP INDEX IF EXISTS idx_pnr_retailer_source_label")
    op.execute("DROP INDEX IF EXISTS idx_pnr_scan_source_label")
    # Restore the legacy UNIQUE (scan_id, normalized_label).
    op.create_index(
        "idx_pnr_scan_label",
        "product_name_resolutions",
        ["scan_id", "normalized_label"],
        unique=True,
    )

    # CHECK match_method : restore previous shape.
    op.execute(
        "ALTER TABLE product_name_resolutions "
        "DROP CONSTRAINT IF EXISTS pnr_match_method_check"
    )
    op.execute(
        f"ALTER TABLE product_name_resolutions "
        f"ADD CONSTRAINT pnr_match_method_check CHECK ({_OLD_MATCH_METHOD})"
    )

    op.execute(
        "ALTER TABLE product_name_resolutions "
        "DROP CONSTRAINT IF EXISTS product_name_resolutions_retailer_id_fkey"
    )
    op.drop_column("product_name_resolutions", "retailer_id")

    op.execute(
        "ALTER TABLE product_name_resolutions "
        "DROP CONSTRAINT IF EXISTS pnr_source_type_check"
    )
    op.drop_column("product_name_resolutions", "source_type")
    # ``pg_trgm`` extension is intentionally left in place — shared by
    # other indexes (brands, products.name).
