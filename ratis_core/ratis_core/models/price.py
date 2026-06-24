from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.product import Product
    from ratis_core.models.scan import Scan
    from ratis_core.models.store import Store


# ============================================================
# PRICE_CONSENSUS
# ============================================================
class PriceConsensus(Base):
    __tablename__ = "price_consensus"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    trust_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("store_id", "product_ean"),
        CheckConstraint("price > 0", name="price_pos"),
        CheckConstraint(
            "trust_score >= 0 AND trust_score <= 100",
            name="trust_range",
        ),
        CheckConstraint("first_seen_at <= last_seen_at", name="seen_order"),
    )

    store: Mapped["Store"] = relationship("Store", back_populates="price_consensus")
    product: Mapped["Product"] = relationship("Product", back_populates="price_consensus")
    scans: Mapped[list["PriceConsensusScans"]] = relationship("PriceConsensusScans", back_populates="consensus")
    history: Mapped[list["PriceConsensusHistory"]] = relationship("PriceConsensusHistory", back_populates="consensus")


# ============================================================
# PRICE_CONSENSUS_SCANS
# ============================================================
class PriceConsensusScans(Base):
    __tablename__ = "price_consensus_scans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consensus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_consensus.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (UniqueConstraint("consensus_id", "scan_id"),)

    consensus: Mapped["PriceConsensus"] = relationship("PriceConsensus", back_populates="scans")
    scan: Mapped["Scan"] = relationship("Scan", back_populates="price_consensus_scans")


# ============================================================
# PRICE_CONSENSUS_HISTORY
# ============================================================
class PriceConsensusHistory(Base):
    __tablename__ = "price_consensus_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consensus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_consensus.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    trust_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("price > 0", name="price_pos"),
        CheckConstraint(
            "trust_score >= 0 AND trust_score <= 100",
            name="trust_range",
        ),
        CheckConstraint("first_seen_at <= last_seen_at", name="seen_order"),
    )

    consensus: Mapped["PriceConsensus"] = relationship("PriceConsensus", back_populates="history")
    store: Mapped["Store"] = relationship("Store", back_populates="price_consensus_history")
    product: Mapped["Product"] = relationship("Product", back_populates="price_consensus_history")
