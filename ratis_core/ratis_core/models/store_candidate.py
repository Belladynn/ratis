"""StoreCandidate — unrecognized stores surfaced by the OCR pipeline.

When the store detection pipeline cannot match a receipt's header to a known
store, it inserts a StoreCandidate for admin review. occurrence_count
increments when the same unrecognized store is seen again (same retailer + CP).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.store import Store


class StoreCandidate(Base):
    __tablename__ = "store_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'matched', 'ignored')",
            name="ck_store_candidates_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_header: Mapped[str] = mapped_column(Text, nullable=False)
    retailer_guess: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_guess: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("receipts.id", ondelete="SET NULL"), nullable=True
    )  # audit-only — no ORM relationship by design; tracks which receipt created this candidate
    matched_store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    matched_store: Mapped["Store | None"] = relationship(
        "Store",
        foreign_keys=[matched_store_id],
        back_populates="candidates",
    )
