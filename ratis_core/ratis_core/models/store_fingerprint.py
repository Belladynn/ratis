"""StoreFingerprint — auto-learning table mapping a confirmed signal to a store.

Analogous to product_observed_names for products. Every confirmed store match
(auto or user-validated) adds a fingerprint for fast O(1) future lookups.

signal_type values:
  'phone'               — "0149970970"
  'store_code'          — "MONOPRIX:2341"
  'barcode_prefix'      — "23410310" (store_code + caisse, first 8 digits of barcode)
  'retailer_postal'     — "MONOPRIX:92400"
  'retailer_postal_num' — "MONOPRIX:92400:10" (with store number)
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.store import Store


class StoreFingerprint(Base):
    __tablename__ = "store_fingerprints"
    __table_args__ = (
        UniqueConstraint("signal_type", "signal_value", name="uq_store_fingerprints_signal"),
        CheckConstraint(
            "signal_type IN ('phone', 'store_code', 'barcode_prefix', 'retailer_postal', 'retailer_postal_num')",
            name="ck_store_fingerprints_signal_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    signal_value: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    store: Mapped["Store"] = relationship("Store", back_populates="fingerprints")
