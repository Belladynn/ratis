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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.product import Brand, Product
    from ratis_core.models.scan import Scan
    from ratis_core.models.user import User


# ============================================================
# GIFT_CARD_BRANDS
# ============================================================
class GiftCardBrand(Base):
    __tablename__ = "gift_card_brands"
    __table_args__ = (
        # Boutique V1 — UNIQUE on the human-readable name so the seed is
        # idempotent via ON CONFLICT (name) and admin UI cannot create
        # duplicate display rows for the same brand.
        UniqueConstraint("name", name="uq_gift_card_brands_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_brand_id: Mapped[str] = mapped_column(Text, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    orders: Mapped[list["GiftCardOrder"]] = relationship("GiftCardOrder", back_populates="brand")


# ============================================================
# GIFT_CARD_ORDERS
# ============================================================
class GiftCardOrder(Base):
    __tablename__ = "gift_card_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'issued', 'failed', 'churned')",
            name="ck_gift_card_orders_status",
        ),
        CheckConstraint(
            "source_type IN ('annual_subscription', 'battlepass_milestone', 'shop_purchase', 'referral_reward')",
            name="ck_gift_card_orders_source_type",
        ),
        UniqueConstraint("source_type", "source_ref_id", name="uq_gift_card_orders_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gift_card_brands.id", ondelete="RESTRICT"), nullable=False
    )
    denomination: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Anti-churn delay: if non-null, a background batch will only issue this
    # order after NOW() >= eligible_at. Used by referral_reward (30 days
    # retention check) — NULL for all other sources (immediate issuance).
    eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Fiscal-cap reservation (audit H4) : amount of the DAS2 annual cap this
    # order has reserved. Set by gift_card_cap_service.reserve_gift_card_cap
    # when the order is issued ; zeroed by release_gift_card_cap on failure.
    # 0 = not (yet) reserved.
    cap_reserved_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="gift_card_orders")
    brand: Mapped["GiftCardBrand"] = relationship("GiftCardBrand", back_populates="orders")


# ============================================================
# AFFILIATE_OFFERS
# ============================================================
class AffiliateOffer(Base):
    __tablename__ = "affiliate_offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=False)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False
    )
    cashback_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        CheckConstraint(
            "provider IN ('affilae', 'awin', 'cj')",
            name="provider_check",
        ),
        CheckConstraint("cashback_rate > 0", name="rate_pos"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="valid_range",
        ),
    )

    product: Mapped["Product"] = relationship("Product", back_populates="affiliate_offers")
    brand: Mapped["Brand"] = relationship("Brand", back_populates="affiliate_offers")
    cashback_transactions: Mapped[list["CashbackTransaction"]] = relationship(
        "CashbackTransaction", back_populates="affiliate_offer"
    )


# ============================================================
# CASHBACK_TRANSACTIONS
# ============================================================
class CashbackTransaction(Base):
    __tablename__ = "cashback_transactions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'refused')",
            name="ck_cashback_transactions_status",
        ),
        CheckConstraint("amount >= 0", name="ck_cashback_transactions_amount_nn"),
        # PG has two CHECKs on ``amount`` from successive migrations — keep
        # both names so the schema-sync test stays clean.
        CheckConstraint("amount > 0", name="amount_pos"),
        CheckConstraint("amount > 0", name="cashback_transactions_amount_check"),
        CheckConstraint(
            "parent_type IN ('boost_parent', 'withdrawal_refund')",
            name="cashback_transactions_parent_type_check",
        ),
        CheckConstraint(
            "type IN ('CREDIT', 'BOOST', 'WITHDRAWAL')",
            name="cashback_transactions_type_check",
        ),
        # CREDIT / BOOST rows MUST carry the affiliate offer + product they
        # originate from — WITHDRAWAL rows have no such context. Mirrors the
        # two PG CHECKs of the same names (Bug 4 + Pattern A roll-out).
        CheckConstraint(
            "type NOT IN ('CREDIT', 'BOOST') OR affiliate_offer_id IS NOT NULL",
            name="credit_requires_offer",
        ),
        CheckConstraint(
            "type NOT IN ('CREDIT', 'BOOST') OR product_ean IS NOT NULL",
            name="credit_requires_product",
        ),
        Index(
            "uq_cashbacktx_scan_ean_credit",
            "scan_id",
            "product_ean",
            unique=True,
            postgresql_where=sa_text("type = 'CREDIT'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NEVER PURGE invariant : ON DELETE SET NULL (audit F-AU-9, migration
    # 20260511_1000_au9npfk). Hard-DELETE of a users row is loud-warned by
    # ``trg_users_warn_hard_delete`` and severs the link rather than
    # cascading the legally-retained financial row away.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    product_ean: Mapped[str | None] = mapped_column(
        Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=True
    )
    affiliate_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("affiliate_offers.id", ondelete="SET NULL"), nullable=True
    )
    boost_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    distributed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="SET NULL"), nullable=True
    )
    parent_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cashback_transactions.id", ondelete="SET NULL"), nullable=True
    )
    parent_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="cashback_transactions")
    product: Mapped["Product | None"] = relationship("Product", back_populates="cashback_transactions")
    affiliate_offer: Mapped["AffiliateOffer | None"] = relationship(
        "AffiliateOffer", back_populates="cashback_transactions"
    )
    scan: Mapped["Scan | None"] = relationship("Scan")
    withdrawal: Mapped["CashbackWithdrawal | None"] = relationship(
        "CashbackWithdrawal", back_populates="cashback_transaction"
    )


# ============================================================
# DISCOUNT_CAMPAIGNS
# ============================================================
class DiscountCampaign(Base):
    __tablename__ = "discount_campaigns"
    __table_args__ = (
        CheckConstraint("code <> ''", name="code_not_empty"),
        CheckConstraint("code = upper(code)", name="code_uppercase"),
        CheckConstraint("label <> ''", name="label_not_empty"),
        CheckConstraint(
            "max_uses IS NULL OR max_uses > 0",
            name="max_uses_pos",
        ),
        # Percentage discounts can't exceed 100 % ; fixed discounts have no
        # cap here (``discount_not_exceed_price`` enforces it per use).
        CheckConstraint(
            "type <> 'percentage' OR value <= 100",
            name="percentage_max",
        ),
        CheckConstraint("type IN ('percentage', 'fixed')", name="type_check"),
        CheckConstraint("uses_count >= 0", name="uses_count_nn"),
        CheckConstraint(
            "max_uses IS NULL OR uses_count <= max_uses",
            name="uses_not_exceed_max",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="valid_range",
        ),
        CheckConstraint("value > 0", name="value_pos"),
    )

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="discount_campaign")


# ============================================================
# SUBSCRIPTIONS
# ============================================================
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NEVER PURGE invariant : ON DELETE SET NULL (audit F-AU-9, migration
    # 20260511_1000_au9npfk). See CashbackTransaction.user_id comment.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("11.99"))
    paid_with: Mapped[str] = mapped_column(Text, nullable=False, default="stripe")
    payment_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_campaign_code: Mapped[str | None] = mapped_column(
        Text, ForeignKey("discount_campaigns.code", ondelete="RESTRICT"), nullable=True
    )
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "paid_with = 'cashback' OR payment_ref IS NOT NULL OR status NOT IN ('active', 'expired')",
            name="payment_ref_coherence",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'cancelled', 'expired')",
            name="subscriptions_status_check",
        ),
        # NOTE: declared in ORM but NOT in PG. Migration ``d0e1f2a3b4c5``
        # promised this constraint in its docstring but the ``op.execute``
        # call was never written. Tracked in ``DECISIONS_PENDING.md`` and
        # whitelisted in ``ORM_ONLY_CONSTRAINTS`` of ``test_schema_sync``.
        CheckConstraint(
            "plan IN ('monthly', 'annual')",
            name="subscriptions_plan_check",
        ),
        # Soft-cancel coherence : ``cancelled_at`` must be set iff
        # ``status='cancelled'``.
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status <> 'cancelled' AND cancelled_at IS NULL)",
            name="cancelled_check",
        ),
        CheckConstraint(
            "discount_amount IS NULL OR discount_amount > 0",
            name="discount_amount_pos",
        ),
        # Either both code + amount are NULL (no discount) or both NOT NULL
        # (discount applied).
        CheckConstraint(
            "(discount_campaign_code IS NOT NULL AND discount_amount IS NOT NULL) "
            "OR (discount_campaign_code IS NULL AND discount_amount IS NULL)",
            name="discount_coherence",
        ),
        # Discount can't make the subscription free.
        CheckConstraint(
            "discount_amount IS NULL OR discount_amount < price",
            name="discount_not_exceed_price",
        ),
        CheckConstraint("expires_at > started_at", name="expires_after_start"),
        CheckConstraint("price > 0", name="price_pos"),
    )

    user: Mapped["User | None"] = relationship("User", back_populates="subscriptions")
    discount_campaign: Mapped["DiscountCampaign | None"] = relationship(
        "DiscountCampaign", back_populates="subscriptions"
    )


# ============================================================
# STRIPE_WEBHOOK_EVENTS
# ============================================================
class StripeWebhookEvent(Base):
    """Idempotency ledger for Stripe webhook events.

    Stripe retries webhook deliveries, so the same ``event_id`` (``evt_...``)
    can arrive multiple times. The ``stripe_webhook`` route claims each
    event_id here on first receipt (INSERT ... ON CONFLICT DO NOTHING) and
    short-circuits on every subsequent arrival — preventing non-idempotent
    side effects (e.g. duplicate annual gift-card issuance).
    """

    __tablename__ = "stripe_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),)


# ============================================================
# CASHBACK_WITHDRAWALS
# ============================================================
class CashbackWithdrawal(Base):
    __tablename__ = "cashback_withdrawals"
    __table_args__ = (
        # PG carries two ``amount > 0`` CHECKs from successive migrations —
        # mirror both names.
        CheckConstraint("amount > 0", name="amount_pos"),
        CheckConstraint("amount > 0", name="cashback_withdrawals_amount_check"),
        # failure_reason is populated iff status='failed'.
        CheckConstraint(
            "(status = 'failed' AND failure_reason IS NOT NULL) OR (status <> 'failed' AND failure_reason IS NULL)",
            name="failure_check",
        ),
        # processed_at is populated iff status='processed'.
        CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL) OR (status <> 'processed' AND processed_at IS NULL)",
            name="processed_check",
        ),
        # Reconciliation : provider ref + initiation timestamp are set
        # together or not at all.
        CheckConstraint(
            "(payment_provider_ref IS NOT NULL AND provider_initiated_at IS NOT NULL) "
            "OR (payment_provider_ref IS NULL AND provider_initiated_at IS NULL)",
            name="provider_coherence",
        ),
        # ``'abandoned'`` added per migration ``20260511_2200_cashback_abandoned``
        # — RGPD-driven tombstone for pending withdrawals at account
        # deletion time (decision 2026-05-08, ARCH § Cashback abandonment).
        CheckConstraint(
            "status IN ('pending', 'processed', 'failed', 'abandoned')",
            name="status_check",
        ),
        # Once processed, the withdrawal must reference its cashback_transaction
        # row (audit trail — see R10 atomic withdraw flow).
        CheckConstraint(
            "status <> 'processed' OR cashback_transaction_id IS NOT NULL",
            name="transaction_required",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NEVER PURGE invariant : ON DELETE SET NULL (audit F-AU-9, migration
    # 20260511_1000_au9npfk). See CashbackTransaction.user_id comment.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    cashback_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cashback_transactions.id", ondelete="RESTRICT"), nullable=True
    )
    payment_provider_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_initiated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="cashback_withdrawals")
    cashback_transaction: Mapped["CashbackTransaction | None"] = relationship(
        "CashbackTransaction", back_populates="withdrawal"
    )
