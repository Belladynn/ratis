from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.analytics import PriceChallenge
    from ratis_core.models.price import PriceConsensus, PriceConsensusHistory
    from ratis_core.models.rewards import AffiliateOffer, CashbackTransaction
    from ratis_core.models.scan import Scan
    from ratis_core.models.shopping import PriceAlert, ProductTracking, ShoppingListItem
    from ratis_core.models.user import User


# ============================================================
# BRANDS
# ============================================================
class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    products: Mapped[list["Product"]] = relationship("Product", back_populates="brand")
    affiliate_offers: Mapped[list["AffiliateOffer"]] = relationship("AffiliateOffer", back_populates="brand")


# ============================================================
# CATEGORIES
# ============================================================
class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (CheckConstraint("name <> ''", name="name_not_empty"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    parent: Mapped["Category | None"] = relationship("Category", remote_side="Category.id", back_populates="children")
    children: Mapped[list["Category"]] = relationship("Category", back_populates="parent")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="category")


# ============================================================
# PRODUCTS
# ============================================================
class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "storage_type IN ('frozen', 'fresh', 'ambient', 'unmatched')",
            name="ck_products_storage_type",
        ),
        # EAN must be 8–14 digits (GTIN-8 to GTIN-14). Internal SKUs reuse the
        # ``2`` prefix (see ``internal_ean_prefix``).
        CheckConstraint(r"ean ~ '^\d{8,14}$'", name="ean_format"),
        # Internal SKUs (source='internal') must start with ``2`` to avoid
        # colliding with real GTINs.
        CheckConstraint(
            "source <> 'internal' OR ean LIKE '2%'",
            name="internal_ean_prefix",
        ),
        # Internal SKUs are weighted/loose products → must declare a unit
        # (kg / l / unit).
        CheckConstraint(
            "source <> 'internal' OR unit IS NOT NULL",
            name="internal_has_unit",
        ),
        CheckConstraint("name <> ''", name="name_not_empty"),
        # OFF-family catalogue products (off / obp / opf / opff) have
        # prepackaged quantities encoded in ``product_quantity`` +
        # ``product_quantity_unit``; the legacy ``unit`` column stays NULL.
        # Constraint renamed from ``off_no_unit`` → ``catalogue_no_unit`` by
        # migration ``20260511_0900_obp_opf`` when OBP/OPF/OPFF sources
        # landed.
        CheckConstraint(
            "source NOT IN ('off', 'obp', 'opf', 'opff') OR unit IS NULL",
            name="catalogue_no_unit",
        ),
        CheckConstraint(
            "source IN ('off', 'obp', 'opf', 'opff', 'internal')",
            name="source_check",
        ),
        CheckConstraint(
            "unit IN ('kg', 'l', 'unit') OR unit IS NULL",
            name="unit_check",
        ),
        # Partial index — only populated rows matter (most products are
        # OFF-seeded with NULL discoverer). Mirrors migration
        # ``20260510_2100_pfd``.
        Index(
            "idx_products_first_discovered",
            "first_discovered_by_user_id",
            postgresql_where=text("first_discovered_by_user_id IS NOT NULL"),
        ),
    )

    ean: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, default="off")
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_quantity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    product_quantity_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    allergens_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    ingredients_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    categories_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    labels_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    # OFF ``origins_tags`` array — drives the Phase C-2 ``attribute:french``
    # mission qualifier (cf ARCH_missions.md § Évolutions Phase C-2 and
    # ``services.product_attributes.is_french_product``). Populated by
    # ratis_batch_off_sync going forward + ratis_batch_origins_backfill
    # for historical rows. Migration ``20260511_2400_phase_c2_origins_tags``.
    origins_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    brands: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    photo_url_small: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OFF multi-field enrichment (migration 20260501_1000_offmf) — fed by
    # ratis_batch_off_sync and consumed by ratis_core.products.pick_display_name
    # to compose the best-quality display label for the FE.
    product_name_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    generic_name_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    brands_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pipeline (bloc 2) — read-only GENERATED column for fuzzy matching.
    # Uses immutable_unaccent wrapper because PG's unaccent is STABLE, not
    # IMMUTABLE — generated columns require IMMUTABLE. Indexed GIN trigram.
    name_normalized: Mapped[str] = mapped_column(
        Text,
        Computed("UPPER(immutable_unaccent(name))", persisted=True),
        nullable=False,
    )
    # V1.1 — first user to scan this EAN on Ratis (achievement
    # ``exp_unknown_10`` Pionnier·e). Set on first eligible scan + never
    # overwritten (cf ``ratis_core.products.first_discovery``). FK ON
    # DELETE SET NULL : when a user is hard-deleted the attribution is
    # forgotten, the row keeps existing for everyone else (RGPD).
    # Cf migration ``20260510_2100_pfd`` and KP-75.
    first_discovered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    category: Mapped["Category | None"] = relationship("Category", back_populates="products")
    brand: Mapped["Brand | None"] = relationship("Brand", back_populates="products")
    # V1.1 first-discovery attribution. ``foreign_keys=`` is mandatory
    # because ``Product`` already participates in other FK chains via
    # ``Scan.product_ean`` — without it SQLAlchemy can't resolve which
    # FK powers this relationship.
    first_discovered_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[first_discovered_by_user_id],
    )
    scans: Mapped[list["Scan"]] = relationship("Scan", back_populates="product")
    price_consensus: Mapped[list["PriceConsensus"]] = relationship("PriceConsensus", back_populates="product")
    price_consensus_history: Mapped[list["PriceConsensusHistory"]] = relationship(
        "PriceConsensusHistory", back_populates="product"
    )
    shopping_list_items: Mapped[list["ShoppingListItem"]] = relationship("ShoppingListItem", back_populates="product")
    tracking: Mapped[list["ProductTracking"]] = relationship("ProductTracking", back_populates="product")
    affiliate_offers: Mapped[list["AffiliateOffer"]] = relationship("AffiliateOffer", back_populates="product")
    cashback_transactions: Mapped[list["CashbackTransaction"]] = relationship(
        "CashbackTransaction", back_populates="product"
    )
    price_challenges: Mapped[list["PriceChallenge"]] = relationship("PriceChallenge", back_populates="product")
    price_alerts: Mapped[list["PriceAlert"]] = relationship("PriceAlert", back_populates="product")


# ============================================================
# OCR KNOWLEDGE
# ============================================================
class OcrKnowledge(Base):
    """
    OCR correction dictionary: maps a raw OCR text to its canonical corrected form.
    Entries with corrected=NULL are unresolved — pending manual review.
    Populated automatically by the OCR pipeline; enriched manually via df_set_knowledge_correction.
    The type column distinguishes product_name, brand_name, retailer_header, address_token entries.
    """

    __tablename__ = "ocr_knowledge"
    __table_args__ = (
        UniqueConstraint("raw_ocr", "type", name="uq_ocr_knowledge_raw_ocr_type"),
        CheckConstraint(
            "match_type IN ('sequence', 'ngram', 'token')",
            name="ck_ocr_knowledge_match_type",
        ),
        CheckConstraint(
            "source IN ('ocr_arbitrage', 'user_correction', 'manual', 'llm')",
            name="ck_ocr_knowledge_source",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_ocr_knowledge_confidence",
        ),
        CheckConstraint(
            "type IN ('product_name', 'brand_name', 'retailer_header', 'address_token', 'dismissal')",
            name="ck_ocr_knowledge_type",
        ),
        CheckConstraint(
            "dismissal_category IS NULL OR dismissal_category IN "
            "('payment_method', 'total', 'tva_label', 'footer', "
            "'header_meta', 'fidelity', 'other')",
            name="ck_ocr_knowledge_dismissal_category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(Text, nullable=False, default="product_name")
    raw_ocr: Mapped[str] = mapped_column(Text, nullable=False)
    corrected: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Polymorphic entity reference : UUID of the resolved entity whose nature is
    # dictated by `type` (retailer_header → retailers.id ; brand_name → brands.id ;
    # address_token → cities.id; product_name → products.ean is NOT a UUID so this
    # stays NULL for product_name). No formal FK — cross-table polymorphism.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Dismissal sub-category — set only when type='dismissal'. NULL otherwise.
    # Enum enforced by ck_ocr_knowledge_dismissal_category. Used by the LLM
    # filter feedback loop (PR #122).
    dismissal_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ============================================================
# PRODUCT FAVORITES
# ============================================================
class ProductFavorite(Base):
    """User's pinned products. Composite PK (user_id, product_ean). No soft delete."""

    __tablename__ = "product_favorites"
    __table_args__ = (Index("ix_product_favorites_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_ean: Mapped[str] = mapped_column(
        Text,
        ForeignKey("products.ean", ondelete="RESTRICT"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
