from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.analytics import PriceChallenge
    from ratis_core.models.price import PriceConsensus, PriceConsensusHistory
    from ratis_core.models.retailer import Retailer
    from ratis_core.models.scan import LabelSession, Receipt, Scan
    from ratis_core.models.shopping import PriceAlert, UserStorePreference
    from ratis_core.models.store_candidate import StoreCandidate
    from ratis_core.models.store_fingerprint import StoreFingerprint


# ============================================================
# STORES
# ============================================================
class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Denormalized cache of retailers.canonical_name. Kept in sync by the
    # `fn_sync_store_retailer_text` trigger (see migration 20260422_0930).
    # Readers (UI, CSV export, existing queries) keep using this column; writers
    # set `retailer_id` and let the trigger propagate.
    retailer: Mapped[str | None] = mapped_column(Text, nullable=True)
    retailer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retailers.id", ondelete="SET NULL"),
        nullable=True,
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── OSM fields ────────────────────────────────────────────────────────────────
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    siret: Mapped[str | None] = mapped_column(CHAR(14), nullable=True)
    osm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    store_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    opening_hours: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'osm'"))

    # Validation lifecycle (PR-B). 'confirmed' for OSM/admin/legacy stores;
    # 'pending' when freshly suggested by a user via /scan/receipt/.../confirm-store;
    # 'suspicious' when pending too long without consensus accumulation. Cashback
    # gating requires 'confirmed' (defense in depth on top of receipts.store_status).
    validation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'confirmed'"))
    suggested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # pipeline (bloc 2) — read-only GENERATED column for fuzzy matching.
    # Uses immutable_unaccent wrapper because PG's unaccent is STABLE, not
    # IMMUTABLE — generated columns require IMMUTABLE. Indexed GIN trigram.
    name_normalized: Mapped[str] = mapped_column(
        Text,
        Computed("UPPER(immutable_unaccent(name))", persisted=True),
        nullable=False,
    )

    # PostGIS — colonne geography générée depuis lat/lng. Lecture seule,
    # jamais écrite par l'app. Les magasins fantômes (0,0) → NULL.
    # spatial_index=False : l'index GIST est déclaré explicitement dans
    # __table_args__ (et créé par la migration en prod).
    geog: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        Computed(
            "CASE WHEN lat = 0 AND lng = 0 THEN NULL "
            "ELSE ST_SetSRID("
            "ST_MakePoint(lng::double precision, lat::double precision), 4326"
            ")::geography END",
            persisted=True,
        ),
        nullable=True,
    )

    receipts: Mapped[list["Receipt"]] = relationship("Receipt", back_populates="store")
    scans: Mapped[list["Scan"]] = relationship("Scan", back_populates="store")
    label_sessions: Mapped[list["LabelSession"]] = relationship("LabelSession", back_populates="store")
    price_consensus: Mapped[list["PriceConsensus"]] = relationship("PriceConsensus", back_populates="store")
    price_consensus_history: Mapped[list["PriceConsensusHistory"]] = relationship(
        "PriceConsensusHistory", back_populates="store"
    )
    price_challenges: Mapped[list["PriceChallenge"]] = relationship("PriceChallenge", back_populates="store")
    price_alerts: Mapped[list["PriceAlert"]] = relationship("PriceAlert", back_populates="store")
    user_store_preferences: Mapped[list["UserStorePreference"]] = relationship(
        "UserStorePreference", back_populates="store"
    )
    fingerprints: Mapped[list["StoreFingerprint"]] = relationship(
        "StoreFingerprint", back_populates="store", cascade="all, delete-orphan"
    )
    candidates: Mapped[list["StoreCandidate"]] = relationship(
        "StoreCandidate",
        foreign_keys="StoreCandidate.matched_store_id",
        back_populates="matched_store",
    )
    retailer_obj: Mapped["Retailer | None"] = relationship(
        "Retailer",
        back_populates="stores",
    )

    __table_args__ = (
        Index(
            "uq_stores_osm_id",
            "osm_id",
            unique=True,
            postgresql_where=text("osm_id IS NOT NULL"),
        ),
        Index(
            "idx_stores_validation_pending",
            "validation_status",
            postgresql_where=text("validation_status = 'pending'"),
        ),
        # pipeline PR-A — store_code lookup support.
        Index(
            "ix_stores_retailer_store_code",
            "retailer",
            "store_code",
            postgresql_where=text("store_code IS NOT NULL AND retailer IS NOT NULL"),
        ),
        Index(
            "ix_stores_store_code",
            "store_code",
            postgresql_where=text("store_code IS NOT NULL"),
        ),
        # SIRENE PR1 — partial index on siret for INSEE upsert lookups.
        # NB : DB also has a UNIQUE partial `uq_stores_siret` (raw SQL in
        # migration 20260415_2100_*) that already covers point lookups.
        # We declare this non-unique partial here too so the lookup
        # contract is visible at the ORM layer for future readers (the
        # PR2 SIRENE upsert helper will reference this name). Indexes
        # are excluded from Alembic autogenerate (see alembic/env.py),
        # so the migration creates this index explicitly.
        Index(
            "ix_stores_siret_lookup",
            "siret",
            postgresql_where=text("siret IS NOT NULL"),
        ),
        Index("ix_stores_geog", "geog", postgresql_using="gist"),
        CheckConstraint(
            "source IN ('osm', 'sirene', 'overture', 'admin', 'user_suggested')",
            name="ck_stores_source",
        ),
        CheckConstraint(
            "validation_status IN ('pending', 'confirmed', 'suspicious')",
            name="ck_stores_validation_status",
        ),
        CheckConstraint(
            "address IS NULL OR address <> ''",
            name="address_not_empty",
        ),
        CheckConstraint("city IS NULL OR city <> ''", name="city_not_empty"),
        # Soft-delete coherence : ``disabled_at`` must be set iff
        # ``is_disabled`` is true.
        CheckConstraint(
            "(is_disabled = true AND disabled_at IS NOT NULL) OR (is_disabled = false AND disabled_at IS NULL)",
            name="disabled_at_check",
        ),
        CheckConstraint("lat >= -90 AND lat <= 90", name="lat_range"),
        CheckConstraint("lng >= -180 AND lng <= 180", name="lng_range"),
        CheckConstraint("name <> ''", name="name_not_empty"),
        CheckConstraint(
            "postal_code IS NULL OR postal_code <> ''",
            name="postal_not_empty",
        ),
        CheckConstraint(
            "retailer IS NULL OR retailer <> ''",
            name="retailer_not_empty",
        ),
    )


# ============================================================
# STORE VALIDATION HISTORY (audit trail of validation transitions)
# ============================================================
class StoreValidationHistory(Base):
    """Append-only audit row for every ``stores.validation_status`` transition.

    ``meta`` (not ``metadata`` — that name clashes with SQLAlchemy's ``Base.metadata``)
    holds free-form JSON context (e.g. ``{"distinct_eans_count": 22}`` for a
    consensus-driven flip).
    """

    __tablename__ = "store_validation_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
