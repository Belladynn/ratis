"""SIRENE geocoding cache (PR1 of the SIRENE/multi-source plan).

Memoises Géoplateforme bulk-geocoding answers keyed by SIRET so the monthly
``ratis_batch_sirene_sync`` does not re-geocode unchanged addresses. The
``address_hash`` column lets the batch detect SIRENE address changes and
re-geocode only those rows.

See ``docs/superpowers/plans/2026-05-10-sirene-impl.md`` § PR1 / PR5.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, DateTime, Index, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class SireneGeocodeCache(Base):
    """Per-SIRET cached geocoding result.

    Geocoding may fail (Géoplateforme returns no coordinates) — in that case
    the row is still inserted with ``lat``/``lng``/``score`` NULL so the next
    batch run does not retry the same dead address.
    """

    __tablename__ = "sirene_geocode_cache"

    siret: Mapped[str] = mapped_column(CHAR(14), primary_key=True)
    address_hash: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    geocoded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_sirene_geocode_cache_address_hash", "address_hash"),)
