"""Retailer + RetailerAlias models (DA-34).

Normalization of the retailer chain concept :
- `retailers` : one row per retailer / sub-brand, with optional parent hierarchy
  (e.g. Carrefour Market → Carrefour). `slug` is the stable identifier.
- `retailer_aliases` : lowercased strings resolving to a retailer. Source can be
  OSM `brand` tag, receipt header, or manual curation. Hot path for
  `batch_osm_sync` + receipt-header OCR resolution.
- `stores.retailer` (TEXT) stays as a denormalized cache kept in sync by a
  PostgreSQL trigger (see migration 20260422_0930).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    DDL,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.store import Store


class Retailer(Base):
    """A retailer chain or sub-brand (e.g. Carrefour, Carrefour Market)."""

    __tablename__ = "retailers"
    __table_args__ = (
        CheckConstraint(
            r"color_hex IS NULL OR color_hex ~ '^#[0-9A-Fa-f]{6}$'",
            name="ck_retailers_color_hex",
        ),
        Index("idx_retailers_slug", "slug"),
        Index("idx_retailers_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retailers.id", ondelete="SET NULL"),
        nullable=True,
    )
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_hex: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False, server_default=text("'FR'"))
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Self-referencing hierarchy (parent → children).
    parent: Mapped["Retailer | None"] = relationship(
        "Retailer",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list["Retailer"]] = relationship(
        "Retailer",
        back_populates="parent",
    )

    aliases: Mapped[list["RetailerAlias"]] = relationship(
        "RetailerAlias",
        back_populates="retailer",
        cascade="all, delete-orphan",
    )

    stores: Mapped[list["Store"]] = relationship(
        "Store",
        back_populates="retailer_obj",
    )


class RetailerAlias(Base):
    """Lowercased alias string resolving to a retailer.

    Sources: ``osm`` (OSM brand tag), ``sirene`` (INSEE SIRENE dump),
    ``overture`` (Overture Maps — anticipation V3), ``receipt_header``
    (OCR receipt parser), ``manual`` (admin-curated).
    """

    __tablename__ = "retailer_aliases"
    __table_args__ = (
        CheckConstraint(
            "source IN ('osm', 'sirene', 'overture', 'receipt_header', 'manual')",
            name="ck_retailer_aliases_source",
        ),
        Index("idx_retailer_aliases_alias", "alias"),
    )

    retailer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retailers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    retailer: Mapped["Retailer"] = relationship(
        "Retailer",
        back_populates="aliases",
    )


# ── Triggers attached via DDL events so `Base.metadata.create_all()` in tests
# installs them alongside the tables. Production runs them via the Alembic
# migration 20260422_0930_retailers_norm, which is the authoritative source.
# ``CREATE OR REPLACE`` + ``DROP TRIGGER IF EXISTS`` keeps the two paths
# compatible. The event is attached to ``Store.__table__`` because SQLAlchemy
# create_all() emits DDL in FK-dependency order (retailers → stores), so the
# after_create on stores guarantees both tables exist.
# ---------------------------------------------------------------------------
from ratis_core.models.store import Store

_SYNC_STORE_RETAILER_TEXT_FN = DDL(
    """
    CREATE OR REPLACE FUNCTION fn_sync_store_retailer_text()
    RETURNS TRIGGER AS $$
    BEGIN
        -- Only rewrite the TEXT cache when a retailer_id is provided. When
        -- retailer_id is NULL we leave the column unchanged: OCR-derived
        -- strings remain as unresolved hints.
        IF NEW.retailer_id IS NOT NULL THEN
            NEW.retailer := (
                SELECT canonical_name
                FROM retailers
                WHERE id = NEW.retailer_id
            );
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
)
_SYNC_STORE_RETAILER_TEXT_TRIGGER = DDL(
    """
    DROP TRIGGER IF EXISTS trg_stores_sync_retailer_text ON stores;
    CREATE TRIGGER trg_stores_sync_retailer_text
    BEFORE INSERT OR UPDATE OF retailer_id ON stores
    FOR EACH ROW EXECUTE FUNCTION fn_sync_store_retailer_text();
    """
)
_CASCADE_CANONICAL_NAME_FN = DDL(
    """
    CREATE OR REPLACE FUNCTION fn_cascade_retailer_canonical_name_change()
    RETURNS TRIGGER AS $$
    BEGIN
        IF NEW.canonical_name IS DISTINCT FROM OLD.canonical_name THEN
            UPDATE stores
            SET retailer = NEW.canonical_name
            WHERE retailer_id = NEW.id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
)
_CASCADE_CANONICAL_NAME_TRIGGER = DDL(
    """
    DROP TRIGGER IF EXISTS trg_retailers_cascade_name_change ON retailers;
    CREATE TRIGGER trg_retailers_cascade_name_change
    AFTER UPDATE OF canonical_name ON retailers
    FOR EACH ROW EXECUTE FUNCTION fn_cascade_retailer_canonical_name_change();
    """
)
# Functions + triggers must exist after BOTH `stores` and `retailers` tables
# are created. We attach to `retailers` since it is the table introduced in
# this migration (stores pre-exists). SQLAlchemy fires `after_create` per
# table; by hooking on `retailers.__table__` we guarantee stores already
# exists.
event.listen(
    Store.__table__,
    "after_create",
    _SYNC_STORE_RETAILER_TEXT_FN.execute_if(dialect="postgresql"),
)
event.listen(
    Store.__table__,
    "after_create",
    _SYNC_STORE_RETAILER_TEXT_TRIGGER.execute_if(dialect="postgresql"),
)
event.listen(
    Store.__table__,
    "after_create",
    _CASCADE_CANONICAL_NAME_FN.execute_if(dialect="postgresql"),
)
event.listen(
    Store.__table__,
    "after_create",
    _CASCADE_CANONICAL_NAME_TRIGGER.execute_if(dialect="postgresql"),
)
