from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.product import Product
    from ratis_core.models.store import Store
    from ratis_core.models.user import User


# ============================================================
# SHOPPING_LISTS
# ============================================================
class ShoppingList(Base):
    __tablename__ = "shopping_lists"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    has_default_name: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="shopping_lists")
    items: Mapped[list["ShoppingListItem"]] = relationship("ShoppingListItem", back_populates="shopping_list")
    optimized_routes: Mapped[list["OptimizedRoute"]] = relationship("OptimizedRoute", back_populates="shopping_list")


# ============================================================
# SHOPPING_LIST_ITEMS
# ============================================================
class ShoppingListItem(Base):
    __tablename__ = "shopping_list_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shopping_lists.id", ondelete="CASCADE"), nullable=False
    )
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False, default=1)
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("list_id", "product_ean"),
        # ``checked_at`` is populated iff the item has been ticked.
        CheckConstraint(
            "(checked = true AND checked_at IS NOT NULL) OR (checked = false AND checked_at IS NULL)",
            name="checked_at_check",
        ),
        CheckConstraint("quantity > 0", name="quantity_pos"),
    )

    shopping_list: Mapped["ShoppingList"] = relationship("ShoppingList", back_populates="items")
    product: Mapped["Product"] = relationship("Product", back_populates="shopping_list_items")


# ============================================================
# PRODUCT_TRACKING
# ============================================================
class ProductTracking(Base):
    __tablename__ = "product_tracking"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avg_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    avg_frequency_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "product_ean"),
        CheckConstraint(
            "avg_frequency_days IS NULL OR avg_frequency_days > 0",
            name="avg_frequency_pos",
        ),
        CheckConstraint(
            "avg_quantity IS NULL OR avg_quantity > 0",
            name="avg_quantity_pos",
        ),
        # ``deactivated_at`` is populated iff the tracking row was deactivated.
        CheckConstraint(
            "(active = false AND deactivated_at IS NOT NULL) OR (active = true AND deactivated_at IS NULL)",
            name="deactivated_check",
        ),
    )

    user: Mapped["User"] = relationship("User", back_populates="product_tracking")
    product: Mapped["Product"] = relationship("Product", back_populates="tracking")


# ============================================================
# OPTIMIZED_ROUTES
# ============================================================
class OptimizedRoute(Base):
    __tablename__ = "optimized_routes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'computing', 'updating', 'failed')",
            name="ck_optimized_routes_status",
        ),
        CheckConstraint(
            "distance_km IS NULL OR distance_km >= 0",
            name="distance_pos",
        ),
        CheckConstraint("expires_at > computed_at", name="expires_after_computed"),
        # Savings can't be greater than the basket total.
        CheckConstraint("total_savings <= total_price", name="savings_lte_price"),
        CheckConstraint("total_price > 0", name="total_price_pos"),
        CheckConstraint("total_savings >= 0", name="total_savings_pos"),
        # Audit H7 — at most one 'computing' route per list at any given time.
        # The partial predicate keeps the index small and allows any number of
        # historical 'ready' / 'failed' rows for the same list.
        Index(
            "uq_optimized_routes_one_computing_per_list",
            "list_id",
            unique=True,
            postgresql_where=sa_text("status = 'computing'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shopping_lists.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("'ready'"), default="ready")
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_savings: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    steps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="optimized_routes")
    shopping_list: Mapped["ShoppingList"] = relationship("ShoppingList", back_populates="optimized_routes")


# ============================================================
# PRICE_ALERTS
# ============================================================
class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=True
    )
    target_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "product_ean", "store_id", "target_price"),
        CheckConstraint("target_price > 0", name="target_price_pos"),
        # Once triggered, the alert must be deactivated (no spam).
        CheckConstraint(
            "triggered_at IS NULL OR active = false",
            name="triggered_check",
        ),
    )

    user: Mapped["User"] = relationship("User", back_populates="price_alerts")
    product: Mapped["Product"] = relationship("Product", back_populates="price_alerts")
    store: Mapped["Store | None"] = relationship("Store", back_populates="price_alerts")


# ============================================================
# USER_STORE_PREFERENCES
# ============================================================
class UserStorePreference(Base):
    __tablename__ = "user_store_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    preference: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "store_id"),
        CheckConstraint(
            "preference IN ('favourite', 'excluded')",
            name="preference_check",
        ),
    )

    user: Mapped["User"] = relationship("User", back_populates="store_preferences")
    store: Mapped["Store"] = relationship("Store", back_populates="user_store_preferences")
