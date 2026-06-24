"""scan_debug — extended visibility (PR #132).

Revision ID: 20260427_1500_scan_debug_v2
Revises: 20260427_1200_scan_debug
Create Date: 2026-04-27 15:00:00.000000+00:00

PR #132 — extend scan_debug for two reasons :
  1. Original PR #126 keyed scan_debug on scans.id, so a receipt that
     never produced a scan (store-fail path : pipeline runs, no store
     identified, items go to receipts.pending_items, zero scans created)
     could not have a debug row. That's exactly the case where we need
     visibility the most → re-anchor on receipt_id.
  2. The OCR pipeline runs 3 (sometimes 4) preprocessing passes
     (corrected / clahe / binarized / inverted-fallback). PR #126 stored
     only one image. We now keep all of them as a JSONB map so we can
     visualize which pass produced the best OCR blocks per retailer.

Schema delta :
  - ADD column id UUID PRIMARY KEY DEFAULT gen_random_uuid()
    (replaces scan_id PK)
  - ADD column receipt_id UUID NOT NULL FK CASCADE → receipts(id)
  - ALTER scan_id : drop PK, become NULLABLE FK SET NULL → scans(id)
  - ADD column processed_images_r2_keys JSONB
    (the legacy processed_image_r2_key Text column is kept for
     backward-compat reading of pre-PR-132 rows; new writes go to the
     JSONB column. We deliberately do NOT drop the legacy column to keep
     the migration reversible without data loss for rows written between
     PR #126 deploy and PR #132 deploy.)
  - ADD index on receipt_id for the admin fan-out endpoint

Round-trip safety : downgrade restores the original (scan_id PK + Text
processed_image_r2_key) shape, but cannot restore rows that had a NULL
scan_id (no PK to satisfy) — those are dropped on downgrade with a
warning. Acceptable since this table is debug-only / TTL 48h.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260427_1500_scan_debug_v2"
down_revision = "20260427_1200_scan_debug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new id PK column (default gen_random_uuid, requires pgcrypto
    #    or the pgcrypto extension — PG ≥ 13 has gen_random_uuid in core
    #    via pgcrypto OR via 'gen_random_uuid()' from pgcrypto. We stay
    #    safe by using a server_default of pgcrypto's function, falling
    #    back to letting the application supply uuid4.
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid()"
    )

    # 2. Add receipt_id (nullable for now → backfill → enforce NOT NULL).
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD COLUMN receipt_id uuid"
    )

    # 3. Backfill receipt_id from scans (every existing scan_debug row was
    #    keyed off a real scan, so this join cannot lose data).
    op.execute(
        """
        UPDATE scan_debug AS sd
        SET receipt_id = s.receipt_id
        FROM scans AS s
        WHERE s.id = sd.scan_id
          AND sd.receipt_id IS NULL
        """
    )

    # 3b. Drop any rows that could not be backfilled (paranoia : a scan
    #     without a receipt should not exist, but if a developer ran the
    #     pipeline against a non-receipt scan path the row would block
    #     the NOT NULL below). Debug rows are TTL'd anyway.
    op.execute("DELETE FROM scan_debug WHERE receipt_id IS NULL")

    # 4. Drop old PK on scan_id (use IF EXISTS for idempotency / rerun).
    op.execute("ALTER TABLE scan_debug DROP CONSTRAINT IF EXISTS scan_debug_pkey")

    # 5. Promote id to PK, enforce receipt_id NOT NULL + FK CASCADE.
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD CONSTRAINT scan_debug_pkey PRIMARY KEY (id)"
    )
    op.execute("ALTER TABLE scan_debug ALTER COLUMN receipt_id SET NOT NULL")
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD CONSTRAINT scan_debug_receipt_id_fkey "
        "FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE"
    )

    # 6. Make scan_id nullable and replace the FK with ON DELETE SET NULL.
    op.execute(
        "ALTER TABLE scan_debug DROP CONSTRAINT IF EXISTS scan_debug_scan_id_fkey"
    )
    op.execute("ALTER TABLE scan_debug ALTER COLUMN scan_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD CONSTRAINT scan_debug_scan_id_fkey "
        "FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE SET NULL"
    )

    # 7. Add the JSONB processed_images_r2_keys column.
    op.add_column(
        "scan_debug",
        sa.Column("processed_images_r2_keys", postgresql.JSONB, nullable=True),
    )

    # 8. Index receipt_id for the admin /receipts/<id>/debug fan-out.
    op.create_index(
        "idx_scan_debug_receipt_id",
        "scan_debug",
        ["receipt_id"],
    )


def downgrade() -> None:
    # 1. Drop the receipt_id index + JSONB column.
    op.execute("DROP INDEX IF EXISTS idx_scan_debug_receipt_id")
    op.drop_column("scan_debug", "processed_images_r2_keys")

    # 2. Rows with NULL scan_id can't satisfy the legacy PK ; drop them.
    op.execute("DELETE FROM scan_debug WHERE scan_id IS NULL")

    # 3. Drop new id PK + receipt_id column + scan_id SET NULL FK.
    op.execute("ALTER TABLE scan_debug DROP CONSTRAINT IF EXISTS scan_debug_pkey")
    op.execute(
        "ALTER TABLE scan_debug DROP CONSTRAINT IF EXISTS scan_debug_receipt_id_fkey"
    )
    op.execute(
        "ALTER TABLE scan_debug DROP CONSTRAINT IF EXISTS scan_debug_scan_id_fkey"
    )
    op.drop_column("scan_debug", "receipt_id")
    op.drop_column("scan_debug", "id")

    # 4. Restore scan_id NOT NULL + PK + CASCADE FK to mirror PR #126.
    op.execute("ALTER TABLE scan_debug ALTER COLUMN scan_id SET NOT NULL")
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD CONSTRAINT scan_debug_pkey PRIMARY KEY (scan_id)"
    )
    op.execute(
        "ALTER TABLE scan_debug "
        "ADD CONSTRAINT scan_debug_scan_id_fkey "
        "FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE"
    )
