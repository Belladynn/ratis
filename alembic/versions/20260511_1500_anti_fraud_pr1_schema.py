"""anti_fraud_pr1 — receipts dedup/fraud schema foundation

Revision ID: 20260511_1500_afpr1
Revises: 20260511_1100_widen
Create Date: 2026-05-11 15:00:00

PR1 of the anti-fraud receipt-pipeline sprint — schema-only foundation
for the dual-fingerprint + pHash + admin-queue mechanism described in
``webservices/ratis_product_analyser/ARCH_receipt_pipeline.md`` §
"Réconciliation tickets — V1 (dual fingerprint + pHash + admin queue)"
(decisions acted 2026-05-11, merged in PR #286).

This migration is **DDL-only** — no application code reads or writes
these columns yet. The compute / lookup helpers land in PR2-5.

Adds to ``receipts`` (7 new nullable columns) :

- ``parse_fingerprint_user``        VARCHAR(64) — sha256 hex of the 10
  canonical fingerprint components + user_id. Intra-user dedup key
  (UNIQUE partial index — fallback when ``receipt_barcode IS NULL``).
- ``parse_fingerprint_global``      VARCHAR(64) — sha256 hex of the 10
  canonical components WITHOUT user_id. Cross-user lookup for fraud
  detection (NON-unique index — collisions are the signal).
- ``fingerprint_components_jsonb``  JSONB — raw 10 components kept for
  debugging / forensic replay of a contested decision.
- ``image_phash``                   VARCHAR(16) — perceptual hash
  (64-bit, hex-encoded) of the source image. Lookup target for the
  pre-OCR image-reuse check (Hamming distance ≤ 8 over a 30-day window).
- ``device_fingerprint``            VARCHAR(16) — HMAC of the User-Agent
  and a few device signals from the upload context. Flagged when the
  same fingerprint is observed across >3 distinct users in 30 days.
- ``time_precision``                TEXT CHECK ('second' | 'minute') —
  whether the OCR successfully parsed seconds in the receipt time.
  Drives the cross-user policy: exact ``fp_global`` match with both
  receipts at ``'second'`` precision is a strict reject ; mixed or
  ``'minute'`` precision is accept + admin flag (digit-swap plausible).
- ``consolidated_from_ids``         UUID[] — when a rescan upload
  consolidates into an existing receipt, the absorbed receipt ids are
  recorded here for audit + downstream cashback adjustment.

4 indexes on ``receipts`` :

- ``idx_receipts_fp_user`` UNIQUE partial — intra-user dedup ; only
  fires when no ticket barcode is available (otherwise the existing
  ``uq_receipts_receipt_barcode`` already gates duplicates). Triggers
  on collision so the worker can ``UPDATE`` the existing row (rescan
  pattern, DA-18-like fallback).
- ``idx_receipts_fp_global_lookup`` partial — cross-user lookup ;
  collisions surface a fraud-suspicion candidate (not blocked at the
  DB layer — application logic applies the time_precision policy).
- ``idx_receipts_image_phash`` partial — pre-OCR image dedup lookup.
- ``idx_receipts_device_fp`` partial — device-pattern lookup.

Creates ``fraud_suspicions`` table — admin queue for cross-user
duplicate / pHash / device-shared signals. CHECK constraints follow
the ARCH (4 signal kinds × 4 resolution statuses validated 2026-05-11).

Schema choices (cf ARCH § "Schéma DB cible (migrations)") :

- ``evidence_receipt_ids UUID[]`` (not a FK array) — these are the
  cross-user receipts that matched ; we don't want an ``ON DELETE``
  contract here, the audit trail must survive their disappearance.
- ``receipt_id`` keeps ``ON DELETE CASCADE`` — if the offending receipt
  is hard-deleted (rare ; soft-delete preferred), the suspicion record
  becomes meaningless.
- No FK from ``fraud_suspicions`` to ``users`` — RGPD anonymize flow
  (cf migration ``20260511_1000_rgpd_anon_completeness``) makes such
  FKs brittle and the table dereferences user identity through
  ``receipt_id`` anyway. ``admin_operator`` is a free-form TEXT label
  the admin UI fills with the operator's identity (audit-friendly,
  RGPD-stable).
- ``updated_at`` is **not** part of the table per the ARCH spec —
  resolution flow is single-shot (``detected_at`` + ``resolved_at``)
  so an additional ``updated_at`` would add noise. If V2 needs partial
  updates, add it then.

4 indexes on ``fraud_suspicions`` :

- ``idx_fraud_suspicions_status`` partial — admin queue list query
  (``WHERE resolution_status = 'pending'``).
- ``idx_fraud_suspicions_receipt`` — lookups from a receipt to its
  suspicions (admin detail screen, debug).
- ``idx_fraud_suspicions_user_pending`` — efficient per-user pending
  count (used by the device-pattern signal aggregator). Goes through
  ``receipts.user_id`` via JOIN — covered by ``receipt_id`` index
  already, so we instead add a signal-aware partial here.

Downgrade reverses the schema exactly (drops indexes → table → columns).
Data is not preserved (no rows exist yet — this is a schema-foundation
migration shipped before any application code writes to it).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic. ≤32 chars per R08.
revision = "20260511_1500_afpr1"
down_revision = "20260511_1100_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. ALTER receipts — 7 new columns
    # ---------------------------------------------------------------
    op.add_column(
        "receipts",
        sa.Column("parse_fingerprint_user", sa.String(64), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column("parse_fingerprint_global", sa.String(64), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column(
            "fingerprint_components_jsonb",
            postgresql.JSONB(),
            nullable=True,
        ),
    )
    op.add_column(
        "receipts",
        sa.Column("image_phash", sa.String(16), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column("device_fingerprint", sa.String(16), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column("time_precision", sa.Text(), nullable=True),
    )
    op.add_column(
        "receipts",
        sa.Column(
            "consolidated_from_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_receipts_time_precision",
        "receipts",
        "time_precision IS NULL OR time_precision IN ('second', 'minute')",
    )

    # ---------------------------------------------------------------
    # 2. 4 indexes on receipts
    # ---------------------------------------------------------------
    # Intra-user strict dedup — fallback when barcode is absent.
    op.execute(
        """
        CREATE UNIQUE INDEX idx_receipts_fp_user
            ON receipts (parse_fingerprint_user)
            WHERE receipt_barcode IS NULL
              AND parse_fingerprint_user IS NOT NULL
        """
    )
    # Cross-user lookup for fraud detection — NON unique.
    op.execute(
        """
        CREATE INDEX idx_receipts_fp_global_lookup
            ON receipts (parse_fingerprint_global)
            WHERE receipt_barcode IS NULL
              AND parse_fingerprint_global IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_receipts_image_phash
            ON receipts (image_phash)
            WHERE image_phash IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_receipts_device_fp
            ON receipts (device_fingerprint)
            WHERE device_fingerprint IS NOT NULL
        """
    )

    # ---------------------------------------------------------------
    # 3. CREATE fraud_suspicions table (admin queue)
    # ---------------------------------------------------------------
    op.create_table(
        "fraud_suspicions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "evidence_receipt_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("detection_signal", sa.Text(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "resolution_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("admin_operator", sa.Text(), nullable=True),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["receipts.id"],
            ondelete="CASCADE",
            name="fraud_suspicions_receipt_id_fkey",
        ),
        sa.CheckConstraint(
            "detection_signal IN ("
            "'phash', 'fp_global_strict', 'fp_global_minute', 'device_shared'"
            ")",
            name="ck_fraud_suspicions_signal",
        ),
        sa.CheckConstraint(
            "resolution_status IN ("
            "'pending', 'confirmed_fraud', 'cleared', 'escalated_support'"
            ")",
            name="ck_fraud_suspicions_status",
        ),
        sa.CheckConstraint(
            "(resolution_status = 'pending' AND resolved_at IS NULL "
            "  AND admin_operator IS NULL) "
            "OR (resolution_status <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_fraud_suspicions_resolution_coherence",
        ),
    )

    # ---------------------------------------------------------------
    # 4. indexes on fraud_suspicions
    # ---------------------------------------------------------------
    op.execute(
        """
        CREATE INDEX idx_fraud_suspicions_status
            ON fraud_suspicions (resolution_status)
            WHERE resolution_status = 'pending'
        """
    )
    op.create_index(
        "idx_fraud_suspicions_receipt",
        "fraud_suspicions",
        ["receipt_id"],
    )
    op.create_index(
        "idx_fraud_suspicions_signal",
        "fraud_suspicions",
        ["detection_signal", "resolution_status"],
    )
    op.create_index(
        "idx_fraud_suspicions_detected_at",
        "fraud_suspicions",
        [sa.text("detected_at DESC")],
    )


def downgrade() -> None:
    # ---------------------------------------------------------------
    # 4. drop fraud_suspicions indexes + table
    # ---------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS idx_fraud_suspicions_detected_at")
    op.execute("DROP INDEX IF EXISTS idx_fraud_suspicions_signal")
    op.execute("DROP INDEX IF EXISTS idx_fraud_suspicions_receipt")
    op.execute("DROP INDEX IF EXISTS idx_fraud_suspicions_status")
    op.drop_table("fraud_suspicions")

    # ---------------------------------------------------------------
    # 3. drop receipts indexes (4)
    # ---------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS idx_receipts_device_fp")
    op.execute("DROP INDEX IF EXISTS idx_receipts_image_phash")
    op.execute("DROP INDEX IF EXISTS idx_receipts_fp_global_lookup")
    op.execute("DROP INDEX IF EXISTS idx_receipts_fp_user")

    # ---------------------------------------------------------------
    # 2. drop receipts columns + CHECK
    # ---------------------------------------------------------------
    op.execute("ALTER TABLE receipts DROP CONSTRAINT IF EXISTS ck_receipts_time_precision")
    op.drop_column("receipts", "consolidated_from_ids")
    op.drop_column("receipts", "time_precision")
    op.drop_column("receipts", "device_fingerprint")
    op.drop_column("receipts", "image_phash")
    op.drop_column("receipts", "fingerprint_components_jsonb")
    op.drop_column("receipts", "parse_fingerprint_global")
    op.drop_column("receipts", "parse_fingerprint_user")
