"""ScanDebug — alpha debug instrumentation (PR #126, extended in PR #132).

Holds the raw OCR + LLM output for one receipt scan so we can replay what
the pipeline saw on a real receipt.

PR #132 extension :
  - keyed off ``receipt_id`` (always created early in process_receipt)
    rather than ``scan_id`` (sometimes never created, e.g. when store
    detection fails). Visibility is preserved on failure paths.
  - ``processed_images_r2_keys`` (JSONB) replaces the single
    ``processed_image_r2_key`` Text column — we now capture all 4 OCR
    preprocess passes (corrected / clahe / binarized / inverted) instead
    of just one, so we can see which pass produced the best blocks.

Written only when STORE_DEBUG=true ; purged after 48h by ratis_batch_purge.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class ScanDebug(Base):
    __tablename__ = "scan_debug"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Anchor : receipts row exists as soon as the upload lands, before any
    # OCR / store / scan logic runs. Therefore we can persist visibility
    # even on store-fail paths where no scan row is ever created.
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional back-reference to the canonical scan attached to the row.
    # NULL when the pipeline produced zero scans (e.g. store unknown +
    # pending_items path). Kept as SET NULL so deleting a scan doesn't
    # nuke the debug row.
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )
    rich_blocks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    llm_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Phase 2e (ARCH OCR↔LLM Bridge v2) :
    # - ``final_receipt_data``    : the ReceiptData *used* to create the
    #   scan (= LLM output converted, or legacy fallback when LLM was
    #   disabled / crashed). Renamed from the misnamed
    #   ``legacy_receipt_data`` (PR #126) for semantic clarity.
    # - ``legacy_parser_output``  : the actual ``parse_receipt(ocr)``
    #   output, run in parallel for side-by-side comparison in the debug
    #   viewer regardless of whether the LLM ran or not.
    final_receipt_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    legacy_parser_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ocr_passes_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-pass R2 keys :
    #   {"corrected": "debug/<id>.corrected.jpg",
    #    "clahe":     "debug/<id>.clahe.jpg",
    #    "binarized": "debug/<id>.binarized.jpg",
    #    "inverted":  "debug/<id>.inverted.jpg"}     # optional, present
    #                                                # only when fallback ran
    # An entry with value=None means we tried to capture/upload that pass
    # but it failed (kept as a key so the consumer knows the pass ran).
    processed_images_r2_keys: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Legacy field — kept temporarily for backward compatibility with rows
    # written before PR #132. New code MUST write into
    # ``processed_images_r2_keys`` instead. The admin endpoint reads the
    # new column first and falls back to this one for legacy rows.
    processed_image_r2_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
