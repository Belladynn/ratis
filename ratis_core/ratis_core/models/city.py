"""City — reference table mapping postal_code → canonical city name.

Populated from La Poste open data (~50 000 rows for France) and enriched
incrementally by ratis_batch_osm_sync (addr:postcode + addr:city from OSM).

Used by the store detection pipeline: after extracting a postal code from
the receipt header, look up the canonical city name instead of trusting
the noisy OCR rendering.
"""

from __future__ import annotations

from sqlalchemy import Index, PrimaryKeyConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class City(Base):
    __tablename__ = "cities"
    __table_args__ = (
        PrimaryKeyConstraint("postal_code", "city_name", name="pk_cities"),
        Index("ix_cities_postal", "postal_code"),
    )

    postal_code: Mapped[str] = mapped_column(Text, nullable=False)
    city_name: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_code: Mapped[str] = mapped_column(Text, nullable=False, server_default="FR")
