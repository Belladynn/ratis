from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.analytics import PriceChallenge
    from ratis_core.models.price import PriceConsensusScans
    from ratis_core.models.product import Product
    from ratis_core.models.store import Store
    from ratis_core.models.user import User


# ============================================================
# LABEL SESSIONS
# ============================================================
class LabelSession(Base):
    __tablename__ = "label_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Nullable: a batch with no store in geographical radius is still persisted
    # (see scans.store_status='unknown'). Reconciliation happens via receipt OCR.
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=True
    )
    scan_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # No user relationship — FK to users.id dropped by RGPD anonymize (F-AU-3).
    # Query LabelSession.user_id directly if needed (it may be an anon UUID).
    store: Mapped["Store | None"] = relationship("Store", back_populates="label_sessions")
    scans: Mapped[list["Scan"]] = relationship("Scan", back_populates="label_session")


# ============================================================
# RECEIPTS
# ============================================================
class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True
    )
    purchased_at: Mapped[date] = mapped_column(Date, nullable=False)
    tva_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_lines_detected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_r2_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    image_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    photo_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    # Client-generated idempotency key — stable across upload retries so a
    # killed-then-restarted client replaying its queue does not create a
    # duplicate receipt. NULL for legacy clients that don't send one.
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Datetime à la seconde (heure locale ticket, sans TZ).
    # NULL quand l'OCR ne détecte pas l'heure — l'index UNIQUE est partiel WHERE NOT NULL.
    purchased_at_with_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    # ── barcode V1 columns ──
    receipt_barcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ``none_as_null=True`` : Python ``None`` → SQL NULL (not ``'null'::jsonb``).
    # The admin barcode endpoints + retroactive reparse task filter on
    # ``barcode_fields IS NULL`` to find unparsed receipts ; without this flag
    # the default JSONB type writes the literal ``'null'`` JSON value, which
    # is *not* SQL NULL, and the WHERE clause silently misses every row
    # (PR-C bug discovered 2026-04-30 via CI debug print).
    barcode_fields: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    store_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'confirmed'"))
    pending_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    user_store_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pipeline (bloc 2) — link to the immutable Phase-2 state. NULL for
    # legacy v2 receipts ; set when Phase 4 persists a v3 receipt.
    parsed_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parsed_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # ── anti-fraud PR1 columns (dual fingerprint + pHash + device + audit) ──
    # Schema-only foundation in PR1 ; compute helpers land in PR2-5 (cf
    # ``ARCH_receipt_pipeline.md`` § "Réconciliation tickets — V1"). All
    # nullable : populated on the V3 hot-path only, NULL for legacy V2
    # rows and any V3 receipt not yet running through the anti-fraud
    # phase 0/5 helpers.
    parse_fingerprint_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_fingerprint_global: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint_components_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    image_phash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    time_precision: Mapped[str | None] = mapped_column(Text, nullable=True)
    consolidated_from_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    # ── anti-fraud PR5 column (user-triggered rescan counter) ──
    # NOT NULL DEFAULT 0 — atomic UPDATE in the rescan route enforces the
    # cap (settings ``pipeline.anti_fraud.rescan_max_attempts``). No CHECK
    # constraint : the cap is settings-driven so PO can tune it without
    # migration. See migration ``20260511_1900_afpr5``.
    rescan_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index(
            "receipts_semantic_dedup_key",
            "store_id",
            "purchased_at_with_time",
            "total_amount",
            unique=True,
            postgresql_where=text("purchased_at_with_time IS NOT NULL AND total_amount IS NOT NULL"),
        ),
        Index(
            "uq_receipts_receipt_barcode",
            "receipt_barcode",
            unique=True,
            postgresql_where=text("receipt_barcode IS NOT NULL"),
        ),
        CheckConstraint(
            "store_status IN ('confirmed', 'pending', 'unknown')",
            name="ck_receipts_store_status",
        ),
        # OCR can't reliably parse a future date — clamp to today.
        CheckConstraint(
            "purchased_at <= CURRENT_DATE",
            name="purchased_not_future",
        ),
        CheckConstraint(
            "total_amount IS NULL OR total_amount > 0",
            name="total_amount_pos",
        ),
        CheckConstraint(
            "tva_total IS NULL OR tva_total >= 0",
            name="tva_pos",
        ),
        # ── anti-fraud PR1 — Pattern A : mirror PG schema additions ──
        # CHECK : time_precision enum (NULL allowed).
        CheckConstraint(
            "time_precision IS NULL OR time_precision IN ('second', 'minute')",
            name="ck_receipts_time_precision",
        ),
        # 4 partial indexes added in migration 20260511_1500_afpr1. Mirroring
        # them here keeps ``Base.metadata.create_all`` (test setup) producing
        # the same schema as alembic — without these, the unique partial
        # index on (parse_fingerprint_user) would silently disappear in
        # tests using create_all and the dedup invariant would be untested.
        Index(
            "idx_receipts_fp_user",
            "parse_fingerprint_user",
            unique=True,
            postgresql_where=text("receipt_barcode IS NULL AND parse_fingerprint_user IS NOT NULL"),
        ),
        Index(
            "idx_receipts_fp_global_lookup",
            "parse_fingerprint_global",
            postgresql_where=text("receipt_barcode IS NULL AND parse_fingerprint_global IS NOT NULL"),
        ),
        Index(
            "idx_receipts_image_phash",
            "image_phash",
            postgresql_where=text("image_phash IS NOT NULL"),
        ),
        Index(
            "idx_receipts_device_fp",
            "device_fingerprint",
            postgresql_where=text("device_fingerprint IS NOT NULL"),
        ),
        # Idempotent upload replay — a client retrying its queue sends the
        # same (user_id, idempotency_key) ; the partial unique index lets us
        # detect the replay and return the existing receipt instead of
        # creating a duplicate. Scoped per user so keys can't collide across
        # accounts. Partial WHERE NOT NULL so legacy clients are unaffected.
        Index(
            "uq_receipts_user_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    user: Mapped["User | None"] = relationship("User", back_populates="receipts")
    store: Mapped["Store | None"] = relationship("Store", back_populates="receipts")
    scans: Mapped[list["Scan"]] = relationship("Scan", back_populates="receipt")


# ============================================================
# SCANS
# ============================================================
class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Nullable: a label scan with no store in geographical radius is still
    # persisted with store_id=NULL + store_status='unknown'. The user is
    # invited to scan a receipt to validate the store (Part B = receipt-based
    # retroactive reconciliation).
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=True
    )
    product_ean: Mapped[str | None] = mapped_column(
        Text, ForeignKey("products.ean", ondelete="SET NULL"), nullable=True
    )
    scanned_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, default=1)
    tva_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_type: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("receipts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    match_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    photo_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    label_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("label_sessions.id", ondelete="SET NULL"), nullable=True
    )
    label_r2_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    label_image_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ── pipeline columns (bloc 2) ──
    # match_confidence : matcher engine score in [0,1], NULL for legacy v2 rows.
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # parsed_ticket_id : link to the immutable Phase-2 state. NULL for legacy
    # v2 rows + label/manual scans (only receipt-OCR'd scans get a ParsedTicket).
    parsed_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parsed_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Store-resolution status — mirrors receipts.store_status:
    #   'confirmed' → store_id is trusted (geo-match or user-selected)
    #   'pending'   → OCR/geo ambiguous, awaiting disambiguation
    #   'unknown'   → no store matched (store_id IS NULL), awaits receipt reconciliation
    store_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'confirmed'"))
    # User geo at shutter time — PII, never logged. Kept for Part B
    # (retroactive reconciliation: when a receipt comes in for an unknown
    # store, match pending 'unknown' scans within a radius).
    user_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    user_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "store_id", "product_ean", "scanned_at"),
        # status enum — superset (v3 + legacy v2). Bloc 8 will drop legacy values.
        # ``failed`` is a legacy worker status (not in the original CHECK but
        # written by worker code paths) ; tracked here so we don't break v2.
        CheckConstraint(
            "status IN ('pending', 'matched', 'unresolved', 'rejected', 'accepted', 'unmatched', 'failed')",
            name="scans_status_check_v3",
        ),
        # match_method — superset (v3 + legacy v2 + admin override).
        # 'manual_admin' added in migration 20260430_1700_paadmin for the
        # PATCH /api/v1/admin/scans/{id} endpoint (ARCH_admin_endpoints PR3).
        # Bloc 8 will drop legacy v2 values.
        CheckConstraint(
            "match_method IS NULL OR match_method IN "
            "('barcode', 'knowledge', 'consensus_match', 'fuzzy_strict', 'manual_admin', "
            "'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean')",
            name="ck_scans_match_method_v3",
        ),
        # v3 invariant : matched ⟹ ean + match_method NOT NULL.
        # Only fires for the new 'matched' status — legacy 'accepted' is unaffected.
        CheckConstraint(
            "status <> 'matched' OR (product_ean IS NOT NULL AND match_method IS NOT NULL)",
            name="ck_scans_matched_requires_ean_method",
        ),
        # v3 invariant : unresolved/rejected ⟹ rejected_reason NOT NULL.
        # Pending is allowed to have NULL reason (pre-treatment state).
        # Legacy 'unmatched' is unaffected.
        CheckConstraint(
            "status NOT IN ('unresolved', 'rejected') OR rejected_reason IS NOT NULL",
            name="ck_scans_non_matched_requires_reason",
        ),
        # match_confidence in [0, 1] when non-NULL.
        CheckConstraint(
            "match_confidence IS NULL OR (match_confidence >= 0.0 AND match_confidence <= 1.0)",
            name="ck_scans_match_confidence_range",
        ),
        CheckConstraint(
            "store_status IN ('confirmed', 'pending', 'unknown')",
            name="ck_scans_store_status",
        ),
        CheckConstraint(
            "(store_status = 'unknown' AND store_id IS NULL) OR (store_status <> 'unknown' AND store_id IS NOT NULL)",
            name="ck_scans_store_status_consistency",
        ),
        # ``manual_no_scanned_name`` : a manual scan is admin-anchor only —
        # it carries the resolved ``product_ean`` and NEVER a free-form
        # ``scanned_name`` (the label lives on the sibling
        # ``product_name_resolutions.normalized_label`` row). Mirrored
        # here (Bug 6) after the PA-side fixture cleanup ; the previous
        # admin-anchor prod path silently violated this in PG until the
        # 2026-05-11 fix (cf ``name_resolution_admin_service._create_admin
        # _anchor_scan``).
        CheckConstraint(
            "scan_type <> 'manual' OR (product_ean IS NOT NULL AND scanned_name IS NULL)",
            name="manual_no_scanned_name",
        ),
        # ``receipt_required`` : every receipt-typed scan has a
        # ``receipt_id`` (FK to the parent receipts row) ; every
        # non-receipt scan (electronic_label, manual) MUST have
        # ``receipt_id IS NULL`` — the column is meaningless for them
        # and a stale value would mislead consensus / scan-history
        # aggregations.
        CheckConstraint(
            "(scan_type = 'receipt' AND receipt_id IS NOT NULL) OR (scan_type <> 'receipt' AND receipt_id IS NULL)",
            name="receipt_required",
        ),
        CheckConstraint("price >= 0", name="price_pos"),
        CheckConstraint("quantity > 0", name="quantity_pos"),
        CheckConstraint(
            "scan_type IN ('receipt', 'electronic_label', 'manual')",
            name="scan_type_check",
        ),
        CheckConstraint(
            "tva_amount IS NULL OR tva_amount >= 0",
            name="tva_pos",
        ),
        # Only receipt scans carry a TVA amount.
        CheckConstraint(
            "tva_amount IS NULL OR scan_type = 'receipt'",
            name="tva_receipt_only",
        ),
    )

    user: Mapped["User | None"] = relationship("User", back_populates="scans")
    store: Mapped["Store | None"] = relationship("Store", back_populates="scans")
    product: Mapped["Product | None"] = relationship("Product", back_populates="scans")
    receipt: Mapped["Receipt | None"] = relationship("Receipt", back_populates="scans")
    label_session: Mapped["LabelSession | None"] = relationship("LabelSession", back_populates="scans")
    price_consensus_scans: Mapped[list["PriceConsensusScans"]] = relationship(
        "PriceConsensusScans", back_populates="scan"
    )
    price_challenge: Mapped["PriceChallenge | None"] = relationship(
        "PriceChallenge", back_populates="scan", uselist=False
    )
