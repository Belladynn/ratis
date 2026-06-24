from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.product import Product
    from ratis_core.models.scan import Scan
    from ratis_core.models.store import Store
    from ratis_core.models.user import User


# ============================================================
# USER_PUSH_TOKENS
# ============================================================
class UserPushToken(Base):
    __tablename__ = "user_push_tokens"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('ios', 'android', 'web')",
            name="platform_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="push_tokens")


# ============================================================
# USER_PREFERENCES
# ============================================================
class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint(
            "search_radius_km > 0 AND search_radius_km <= 50",
            name="radius_range",
        ),
        CheckConstraint(
            "transport_mode IN ('driving', 'walking', 'cycling')",
            name="transport_check",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    search_radius_km: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    transport_mode: Mapped[str] = mapped_column(Text, nullable=False, default="driving")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="preferences")


# ============================================================
# USER_SESSIONS
# ============================================================
class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('ios', 'android', 'web')",
            name="platform_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="sessions")


# ============================================================
# USER_SESSION_STATS
# ============================================================
class UserSessionStat(Base):
    __tablename__ = "user_session_stats"
    __table_args__ = (
        CheckConstraint("android_count >= 0", name="android_nn"),
        CheckConstraint("ios_count >= 0", name="ios_nn"),
        CheckConstraint(
            "period_month >= 1 AND period_month <= 12",
            name="month_range",
        ),
        CheckConstraint("web_count >= 0", name="web_nn"),
        CheckConstraint(
            "period_year >= 2024 AND period_year <= 2100",
            name="year_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False, primary_key=True)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False, primary_key=True)
    ios_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    android_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped["User"] = relationship("User", back_populates="session_stats")


# ============================================================
# NOTIFICATION_LOGS
# ============================================================
class NotificationLog(Base):
    __tablename__ = "notification_logs"
    # ``notification_logs_type_check`` mirrors the canonical NotifType
    # Literal in ``webservices/ratis_notifier/routes/notify.py`` (plus
    # ``route_ready`` and ``trust_score_warning`` which the legacy
    # ``notify_user()`` callers emit from ratis_list_optimiser and
    # ratis_batch_trust_score). Widened from the obsolete legacy enum
    # (price_drop / streak_reminder / weekly_recap / challenge_available
    # / cashback_credited / level_up) on 2026-05-11 — see alembic
    # ``20260511_1100_widen_reward_notif_check`` migration.
    __table_args__ = (
        CheckConstraint(
            "type IN ('scan_done', 'cashback_available', 'badge_unlocked', "
            "'price_alert', 'route_ready', 'battlepass_milestone_unlocked', "
            "'challenge_milestone_unlocked', 'mystery_product_found', "
            "'store_validated', 'retro_cab_gratitude', "
            "'achievement_unlocked', 'trust_score_warning')",
            name="notification_logs_type_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="sent", server_default="sent")
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expo_ticket_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="notification_logs")


# Dedup index — one "sent" log per (user, type, calendar-minute).
# Prevents concurrent requests from logging the same notification twice.
# Partial: does not constrain "skipped" or "failed" entries.
Index(
    "ix_notification_logs_dedup_sent",
    NotificationLog.user_id,
    NotificationLog.type,
    text("date_trunc('minute', sent_at AT TIME ZONE 'UTC')"),
    unique=True,
    postgresql_where=(NotificationLog.status == "sent"),
)


# ============================================================
# PRICE_CHALLENGES
# ============================================================
class PriceChallenge(Base):
    __tablename__ = "price_challenges"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'validated', 'rejected')",
            name="status_check",
        ),
        CheckConstraint(
            "trust_score >= 0 AND trust_score <= 100",
            name="trust_range",
        ),
        # Validated coherence : validated_price NOT NULL iff status='validated'.
        CheckConstraint(
            "(status = 'validated' AND validated_price IS NOT NULL) "
            "OR (status <> 'validated' AND validated_price IS NULL)",
            name="validated_coherence",
        ),
        CheckConstraint(
            "validated_price IS NULL OR validated_price > 0",
            name="validated_price_pos",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    product_ean: Mapped[str | None] = mapped_column(
        Text, ForeignKey("products.ean", ondelete="SET NULL"), nullable=True
    )
    image_crop_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    validated_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    trust_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    scan: Mapped["Scan"] = relationship("Scan", back_populates="price_challenge")
    store: Mapped["Store"] = relationship("Store", back_populates="price_challenges")
    product: Mapped["Product | None"] = relationship("Product", back_populates="price_challenges")
    responses: Mapped[list["PriceChallengeResponse"]] = relationship(
        "PriceChallengeResponse", back_populates="challenge"
    )


# ============================================================
# PRICE_CHALLENGE_RESPONSES
# ============================================================
class PriceChallengeResponse(Base):
    __tablename__ = "price_challenge_responses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_challenges.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id"),
        CheckConstraint("price > 0", name="price_pos"),
    )

    challenge: Mapped["PriceChallenge"] = relationship("PriceChallenge", back_populates="responses")
    user: Mapped["User | None"] = relationship("User", back_populates="price_challenge_responses")


# ============================================================
# UNKNOWN_SCANS_WEEKLY_AGGREGATE — Part B retention
# ============================================================
# When a label scan saved as store_status='unknown' ages past the 7-day
# reconciliation window, it is hard-deleted by the daily purge batch.
# Before deletion, counts are rolled up here so product analytics can
# still reason about "N unknown scans per ISO week" without retaining
# the user's PII geo on the scan row.
#
# year_week is ISO format "YYYY-Www" (e.g. "2026-W16"). scan_count is
# the total unknown scans for that week across all users. count_per_scan_type
# breaks it down by scan_type (JSONB for schema flexibility — e.g. new
# scan_type values can land without a migration).
class UnknownScansWeeklyAggregate(Base):
    __tablename__ = "unknown_scans_weekly_aggregate"

    year_week: Mapped[str] = mapped_column(Text, primary_key=True)
    scan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_per_scan_type: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
