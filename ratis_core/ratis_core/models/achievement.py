"""Achievement catalog + user_achievements instances.

Achievements V1 — catalog-driven achievement system.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md.

The catalog table (``achievements``) holds 23+ seeded entries; per-user
unlocks are stored in ``user_achievements`` with a snapshot of the CAB
granted (so future grille reprices don't rewrite history) and the trigger
event JSONB (debug audit, truncated to 2KB by the service).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

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
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

# Postgres ENUMs.
# ``create_type=True`` lets SQLAlchemy create the type on ``Base.metadata.
# create_all`` (test setup) ; in prod the alembic migration creates them
# first via raw ``CREATE TYPE`` SQL, and SQLAlchemy's ``checkfirst=True``
# default makes ``create_all`` a no-op for already-existing types. Same
# pattern as ``admin_audit.py``.
RarityEnum = ENUM(
    "terracotta",
    "bronze",
    "copper",
    "silver",
    "gold",
    "emerald",
    "sapphire",
    "ruby",
    "crystal",
    "diamond",
    name="achievement_rarity",
    create_type=True,
)
CategoryEnum = ENUM(
    "volume",
    "savings",
    "streak",
    "social",
    "exploration",
    "seasonal",
    "secret",
    "j_y_etais",
    name="achievement_category",
    create_type=True,
)
TriggerTypeEnum = ENUM(
    "scan_count",
    "savings_eur_total",
    "savings_eur_in_window",
    "streak_days",
    "referral_count",
    "unique_brands_count",
    "unique_categories_count",
    "unique_products_discovered_count",
    "first_event",
    name="achievement_trigger_type",
    create_type=True,
)


class Achievement(Base):
    """Catalog row — one per achievement available to the platform."""

    __tablename__ = "achievements"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str] = mapped_column(Text, nullable=False)
    rarity: Mapped[str] = mapped_column(RarityEnum, nullable=False)
    category: Mapped[str] = mapped_column(CategoryEnum, nullable=False)
    trigger_type: Mapped[str] = mapped_column(TriggerTypeEnum, nullable=False)
    target_value: Mapped[float] = mapped_column(Numeric, nullable=False)
    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cab_reward: Mapped[int] = mapped_column(Integer, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    user_unlocks: Mapped[list["UserAchievement"]] = relationship(back_populates="achievement", cascade="save-update")

    __table_args__ = (
        CheckConstraint("target_value > 0", name="ck_achievements_target_positive"),
        CheckConstraint("cab_reward >= 0", name="ck_achievements_cab_nonneg"),
        CheckConstraint(
            "window_days IS NULL OR window_days > 0",
            name="ck_achievements_window_positive",
        ),
        CheckConstraint(
            "available_until IS NULL OR available_from IS NULL OR available_until > available_from",
            name="ck_achievements_window_consistent",
        ),
        CheckConstraint(
            "category != 'j_y_etais'",
            name="ck_achievements_no_jyetais_in_catalog",
        ),
        Index("idx_achievements_trigger_type", "trigger_type"),
        Index("idx_achievements_category", "category"),
        # Partial index — mirrors prod migration 20260510_1000_ach_v1 so
        # ``Base.metadata.create_all`` (test setup) produces the same
        # schema as alembic. Without this, schema would silently drift
        # between prod (where index exists) and tests (where it does not).
        Index(
            "idx_achievements_window",
            "available_from",
            "available_until",
            postgresql_where=text("available_from IS NOT NULL OR available_until IS NOT NULL"),
        ),
    )


class UserAchievement(Base):
    """Per-user unlock instance — created once on first satisfaction.

    Idempotent via ``UNIQUE (user_id, achievement_id)``. The service uses
    ``INSERT ... ON CONFLICT DO NOTHING`` so concurrent unlock attempts
    silently no-op without doubling the CAB grant.
    """

    __tablename__ = "user_achievements"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # that has no corresponding ``users`` row (cf migration
    # ``20260511_1000_rgpd_anon_completeness`` + ``ratis_core.anonymize``).
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=False,
    )
    achievement_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("achievements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    cab_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_event: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    achievement: Mapped["Achievement"] = relationship(back_populates="user_unlocks")

    __table_args__ = (
        UniqueConstraint("user_id", "achievement_id", name="uq_user_achievements_pair"),
        Index(
            "idx_user_achievements_user",
            "user_id",
            text("unlocked_at DESC"),
        ),
        Index("idx_user_achievements_achievement", "achievement_id"),
    )
