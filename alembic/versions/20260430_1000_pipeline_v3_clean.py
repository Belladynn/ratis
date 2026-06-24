"""pipeline_v3 clean install — bloc 2 of ARCH_receipt_pipeline.md

Revision ID: 20260430_1000_pipev3
Revises: 20260429_1000_storeval
Create Date: 2026-04-30 10:00:00.000000+00:00

This migration is a CLEAN INSTALL of the pipeline_v3 DB layer (cf.
``ARCH_receipt_pipeline.md`` § "Bloc 2 — DB"). It is the bridge between
the alpha test schema (legacy v2 statuses ``accepted``/``unmatched``)
and the new v3 schema aligned with the immutable Pydantic types in
``webservices/ratis_product_analyser/worker/pipeline_v3/types.py``.

Operations (single transaction):

1.  TRUNCATE alpha tables (scans, receipts, price_consensus*,
    cashback_transactions, cabecoin_transactions, …) — destructive.
    Authorised via DA-36 in ``DECISIONS_ACTED.md`` (alpha override of the
    NEVER PURGE rule for these legacy tables).
2.  Reset materialised balances (``user_cab_balance``,
    ``user_cashback_balance``).
3.  Install ``unaccent`` extension (``pg_trgm`` is already installed).
4.  Drop legacy CHECK constraints on ``scans`` that are incompatible
    with v3 statuses, then install superset CHECK constraints that
    accept BOTH legacy and v3 values. Bloc 8 (later) will drop the
    legacy values once v2 callsites are decommissioned.
5.  Add columns ``scans.match_confidence`` and ``scans.parsed_ticket_id``
    (FK to ``parsed_tickets`` created later in this migration).
6.  Create table ``parsed_tickets`` — immutable Phase-2 state, the
    Cardinal state per ARCH § Traçabilité. ``parsed_jsonb_hash`` is
    UNIQUE (idempotence — re-running Phase 2 on the same image yields
    the same hash and the upsert is a no-op).
7.  Wire ``scans.parsed_ticket_id`` and add ``receipts.parsed_ticket_id``
    (both FK ON DELETE SET NULL).
8.  Create append-only ``pipeline_audit_log`` with a trigger that
    forbids UPDATE (DELETE remains tolerated for retention/admin
    cleanup).
9.  Add GENERATED stored columns ``products.name_normalized`` and
    ``stores.name_normalized`` (``UPPER(immutable_unaccent(name))``) plus GIN
    trigram indexes for fuzzy matching.

Transitional CHECK semantics (cf. ARCH § "Coexistence v2/v3"):

* ``status`` enum is the **superset**
  ``('pending', 'matched', 'unresolved', 'rejected', 'accepted', 'unmatched')``.
  The two legacy values are kept transient so the v2 worker keeps
  writing valid rows ; bloc 8 will ALTER them out.
* ``match_method`` enum is the superset
  ``('barcode', 'knowledge', 'fuzzy_strict',
     'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean')``.
* The new invariants
  (``ck_scans_matched_requires_ean_method``,
   ``ck_scans_non_matched_requires_reason``,
   ``ck_scans_match_confidence_range``)
  ONLY trigger on the v3 statuses (``matched``, ``unresolved``,
  ``rejected``) — they leave legacy ``accepted`` / ``unmatched`` rows
  untouched.

Downgrade behaviour : the schema is restored (drops the new tables /
columns / triggers / extension) but the truncated data is **not**
restored — alpha data is lost by design. Documented in DA-36.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260430_1000_pipev3"
down_revision = "20260429_1000_storeval"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Static lists kept here so upgrade() / downgrade() stay readable.
# ---------------------------------------------------------------------------

_TRUNCATE_TABLES = [
    "mystery_challenge_finds",
    "price_challenges",
    "price_consensus_scans",
    "price_consensus_history",
    "price_consensus",
    "ocr_knowledge",
    "price_alerts",
    "optimized_routes",
    "unknown_scans_weekly_aggregate",
    "store_candidates",
    "cashback_transactions",
    "cabecoin_transactions",
    "receipts",
    "scans",
]
"""Order is informational only — TRUNCATE … CASCADE handles FK chains.
RESTART IDENTITY resets sequences for SERIAL columns (defensive — most
PKs here are UUID, but cheap insurance)."""

_LEGACY_SCAN_CHECKS_TO_DROP = (
    "accepted_requires_ean",
    "ck_scans_match_method",
    "rejected_reason_check",
    "unmatched_not_manual",
    "unmatched_requires_null_ean",
    "unmatched_requires_scanned_name",
    "scans_status_check",
)
"""Legacy CHECK constraints on scans that need to be relaxed/replaced.
``ck_scans_store_status_consistency`` is intentionally KEPT — it is
valid for both v2 and v3 (stores match works the same way)."""

_V3_STATUS_VALUES = ("pending", "matched", "unresolved", "rejected")
_LEGACY_STATUS_VALUES = ("accepted", "unmatched", "failed")
"""``failed`` is a legacy v2 worker status used when the pipeline cannot
even produce a usable parse (corrupt PDF, blurry image, etc.). It was
present in production code but never encoded in the ``scans_status_check``
CHECK constraint of the legacy schema (cf. SA dev finding 2026-04-30 :
prod CHECK was permissive, the worker write paths exploited the gap).
Bloc 8 will fold ``failed`` into ``rejected`` with an explicit reason."""
_SUPERSET_STATUS_VALUES = _V3_STATUS_VALUES + _LEGACY_STATUS_VALUES

_V3_MATCH_METHODS = ("barcode", "knowledge", "fuzzy_strict")
_LEGACY_MATCH_METHODS = (
    "observed_name", "fuzzy", "fuzzy_confirmed", "manual", "barcode_ean",
)
_SUPERSET_MATCH_METHODS = _V3_MATCH_METHODS + _LEGACY_MATCH_METHODS


def _sql_string_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ── 1. Truncate alpha tables ─────────────────────────────────────────────
    op.execute(
        "TRUNCATE TABLE "
        + ", ".join(_TRUNCATE_TABLES)
        + " RESTART IDENTITY CASCADE"
    )

    # ── 2. Reset materialised balances ───────────────────────────────────────
    op.execute("UPDATE user_cab_balance SET balance = 0, updated_at = now()")
    op.execute("UPDATE user_cashback_balance SET balance = 0, updated_at = now()")

    # ── 3. Extension + immutable wrapper ─────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    # PostgreSQL marks ``unaccent()`` as STABLE (its dictionary is loaded from
    # disk at install time). GENERATED columns require IMMUTABLE expressions —
    # we therefore wrap the call in an immutable SQL function. The dictionary
    # name is hard-coded and cannot change at runtime, so the wrapper IS
    # functionally immutable. Pattern recommended by the PG docs (see
    # https://www.postgresql.org/docs/current/unaccent.html § "Functions").
    op.execute(
        """
        CREATE OR REPLACE FUNCTION immutable_unaccent(text)
        RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent', $1) $$;
        """
    )

    # ── 4. Drop legacy CHECK constraints on scans (defensive IF EXISTS) ──────
    for name in _LEGACY_SCAN_CHECKS_TO_DROP:
        op.execute(f"ALTER TABLE scans DROP CONSTRAINT IF EXISTS {name}")

    # ── 5. New scan columns (parsed_ticket_id FK wired AFTER parsed_tickets) ─
    op.add_column(
        "scans",
        sa.Column("match_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "scans",
        sa.Column(
            "parsed_ticket_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # ── 6. New CHECK constraints on scans (transitional supersets) ───────────
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT scans_status_check_v3 "
        f"CHECK (status IN ({_sql_string_list(_SUPERSET_STATUS_VALUES)}))"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method_v3 "
        "CHECK (match_method IS NULL OR match_method IN "
        f"({_sql_string_list(_SUPERSET_MATCH_METHODS)}))"
    )
    # matched ⟹ ean + match_method NOT NULL — only fires for v3 'matched'.
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_matched_requires_ean_method "
        "CHECK (status <> 'matched' OR "
        "(product_ean IS NOT NULL AND match_method IS NOT NULL))"
    )
    # unresolved/rejected ⟹ rejected_reason NOT NULL — v3 statuses only.
    # Legacy 'accepted' / 'unmatched' / 'pending' rows are not constrained.
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_non_matched_requires_reason "
        "CHECK (status NOT IN ('unresolved', 'rejected') "
        "OR rejected_reason IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_confidence_range "
        "CHECK (match_confidence IS NULL OR "
        "(match_confidence >= 0.0 AND match_confidence <= 1.0))"
    )

    # ── 7. parsed_tickets table ──────────────────────────────────────────────
    op.create_table(
        "parsed_tickets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parsed_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("parsed_jsonb_hash", sa.Text(), nullable=False),
        sa.Column("raw_ticket_image_hash", sa.Text(), nullable=False),
        sa.Column("ocr_engine_version", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["receipts.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "parsed_jsonb_hash", name="uq_parsed_tickets_jsonb_hash"
        ),
    )
    op.create_index(
        "ix_parsed_tickets_receipt_id", "parsed_tickets", ["receipt_id"]
    )
    op.create_index(
        "ix_parsed_tickets_image_hash",
        "parsed_tickets",
        ["raw_ticket_image_hash"],
    )
    op.create_index(
        "ix_parsed_tickets_created_at", "parsed_tickets", ["created_at"]
    )

    # Wire scans.parsed_ticket_id FK now that parsed_tickets exists.
    op.create_foreign_key(
        "fk_scans_parsed_ticket",
        "scans",
        "parsed_tickets",
        ["parsed_ticket_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_scans_parsed_ticket_id", "scans", ["parsed_ticket_id"]
    )

    # Same for receipts.
    op.add_column(
        "receipts",
        sa.Column(
            "parsed_ticket_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_receipts_parsed_ticket",
        "receipts",
        "parsed_tickets",
        ["parsed_ticket_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_receipts_parsed_ticket_id", "receipts", ["parsed_ticket_id"]
    )

    # ── 8. pipeline_audit_log (append-only) ──────────────────────────────────
    op.create_table(
        "pipeline_audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "parsed_ticket_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["parsed_ticket_id"], ["parsed_tickets.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "phase IN ('extract', 'comprehend', 'match', 'persist')",
            name="ck_pipeline_audit_log_phase",
        ),
        sa.CheckConstraint(
            "level IN ('verbose', 'normal', 'production')",
            name="ck_pipeline_audit_log_level",
        ),
    )
    op.create_index(
        "ix_pipeline_audit_log_parsed_ticket_id",
        "pipeline_audit_log",
        ["parsed_ticket_id"],
    )
    op.create_index(
        "ix_pipeline_audit_log_scan_id", "pipeline_audit_log", ["scan_id"]
    )
    op.create_index(
        "ix_pipeline_audit_log_created_at",
        "pipeline_audit_log",
        ["created_at"],
    )
    op.create_index(
        "ix_pipeline_audit_log_phase_event",
        "pipeline_audit_log",
        ["phase", "event"],
    )

    # Append-only enforcement : block UPDATE via trigger. DELETE stays
    # allowed for retention / admin cleanup.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_pipeline_audit_log_no_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'pipeline_audit_log is append-only — UPDATE prohibited';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_pipeline_audit_log_no_update
        BEFORE UPDATE ON pipeline_audit_log
        FOR EACH ROW EXECUTE FUNCTION fn_pipeline_audit_log_no_update();
        """
    )

    # ── 9. GENERATED columns name_normalized + GIN trigram index ─────────────
    op.execute(
        "ALTER TABLE products ADD COLUMN name_normalized TEXT "
        "GENERATED ALWAYS AS (UPPER(immutable_unaccent(name))) STORED"
    )
    op.execute(
        "ALTER TABLE stores ADD COLUMN name_normalized TEXT "
        "GENERATED ALWAYS AS (UPPER(immutable_unaccent(name))) STORED"
    )
    op.execute(
        "CREATE INDEX ix_products_name_normalized_trgm "
        "ON products USING GIN (name_normalized gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_stores_name_normalized_trgm "
        "ON stores USING GIN (name_normalized gin_trgm_ops)"
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # NOTE: downgrade restores the legacy schema but does NOT recreate the
    # truncated alpha data (scans / receipts / cashback_transactions /
    # cabecoin_transactions / price_consensus*). Alpha data is lost by
    # design (validated by the orchestrator + user 2026-04-30, DA-36).

    # ── 9. drop GENERATED columns + indexes ──────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_stores_name_normalized_trgm")
    op.execute("DROP INDEX IF EXISTS ix_products_name_normalized_trgm")
    op.execute("ALTER TABLE stores DROP COLUMN IF EXISTS name_normalized")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS name_normalized")

    # ── 8. drop pipeline_audit_log + trigger function ────────────────────────
    op.execute(
        "DROP TRIGGER IF EXISTS trg_pipeline_audit_log_no_update ON pipeline_audit_log"
    )
    op.execute("DROP FUNCTION IF EXISTS fn_pipeline_audit_log_no_update()")
    op.drop_index(
        "ix_pipeline_audit_log_phase_event", table_name="pipeline_audit_log"
    )
    op.drop_index(
        "ix_pipeline_audit_log_created_at", table_name="pipeline_audit_log"
    )
    op.drop_index(
        "ix_pipeline_audit_log_scan_id", table_name="pipeline_audit_log"
    )
    op.drop_index(
        "ix_pipeline_audit_log_parsed_ticket_id",
        table_name="pipeline_audit_log",
    )
    op.drop_table("pipeline_audit_log")

    # ── 7. unwire FKs + drop parsed_tickets ──────────────────────────────────
    op.drop_index("ix_receipts_parsed_ticket_id", table_name="receipts")
    op.execute(
        "ALTER TABLE receipts DROP CONSTRAINT IF EXISTS fk_receipts_parsed_ticket"
    )
    op.drop_column("receipts", "parsed_ticket_id")

    op.drop_index("ix_scans_parsed_ticket_id", table_name="scans")
    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS fk_scans_parsed_ticket"
    )

    op.drop_index(
        "ix_parsed_tickets_created_at", table_name="parsed_tickets"
    )
    op.drop_index(
        "ix_parsed_tickets_image_hash", table_name="parsed_tickets"
    )
    op.drop_index(
        "ix_parsed_tickets_receipt_id", table_name="parsed_tickets"
    )
    op.drop_table("parsed_tickets")

    # ── 6. drop new scan CHECK constraints ───────────────────────────────────
    for name in (
        "ck_scans_match_confidence_range",
        "ck_scans_non_matched_requires_reason",
        "ck_scans_matched_requires_ean_method",
        "ck_scans_match_method_v3",
        "scans_status_check_v3",
    ):
        op.execute(f"ALTER TABLE scans DROP CONSTRAINT IF EXISTS {name}")

    # ── 5. drop new scan columns ─────────────────────────────────────────────
    op.drop_column("scans", "parsed_ticket_id")
    op.drop_column("scans", "match_confidence")

    # ── 4. restore legacy CHECK constraints on scans ─────────────────────────
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT scans_status_check "
        "CHECK (status IN ('pending', 'unmatched', 'accepted', 'rejected'))"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method "
        "CHECK (match_method IN ('observed_name', 'fuzzy', 'fuzzy_confirmed', "
        "'manual', 'barcode_ean'))"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT accepted_requires_ean "
        "CHECK (status <> 'accepted' OR product_ean IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT rejected_reason_check "
        "CHECK ((status = 'rejected' AND rejected_reason IS NOT NULL) OR "
        "(status <> 'rejected' AND rejected_reason IS NULL))"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT unmatched_not_manual "
        "CHECK (NOT (status = 'unmatched' AND scan_type = 'manual'))"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT unmatched_requires_null_ean "
        "CHECK ((status = 'unmatched' AND product_ean IS NULL) OR "
        "status <> 'unmatched')"
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT unmatched_requires_scanned_name "
        "CHECK (status <> 'unmatched' OR scanned_name IS NOT NULL)"
    )

    # ── 3. drop immutable wrapper + unaccent extension ───────────────────────
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
    op.execute("DROP EXTENSION IF EXISTS unaccent")

    # ── 2/1. balances + truncated data are NOT restored (see top of fn). ────
