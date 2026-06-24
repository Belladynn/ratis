from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    from ratis_core.models.user import User


# ============================================================
# LEVEL_TIERS — declared before User (deferred FK in schema)
# ============================================================
class LevelTier(Base):
    __tablename__ = "level_tiers"
    __table_args__ = (
        CheckConstraint("cab_threshold >= 0", name="cab_threshold_nn"),
        CheckConstraint("label <> ''", name="label_not_empty"),
        CheckConstraint("level > 0", name="level_pos"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    level: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    cab_threshold: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    users: Mapped[list["User"]] = relationship("User", back_populates="current_level")


# ============================================================
# REWARD_CONFIG
# ============================================================
class RewardConfig(Base):
    __tablename__ = "reward_config"
    # ``reward_config_action_type_check`` mirrors the canonical missions
    # catalogue set (see ``missions_action_type_check`` below and the
    # alembic ``20260511_1100_widen_reward_notif_check`` migration).
    # The legacy ``DAILY_LOGIN/SCAN_RECEIPT/VIDEO_SCAN/PRICE_CHALLENGE``
    # enum was widened on 2026-05-11 — KP-08 multi-place sync applies.
    __table_args__ = (
        CheckConstraint("base_amount > 0", name="base_amount_pos"),
        CheckConstraint(
            "action_type IN ('receipt_scan', 'label_scan', 'barcode_scan', "
            "'product_identification', 'price_compared', "
            "'fill_product_field', 'scan_distinct', 'promo_found')",
            name="reward_config_action_type_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_type: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ============================================================
# STREAK_TIERS
# ============================================================
class StreakTier(Base):
    __tablename__ = "streak_tiers"
    __table_args__ = (
        CheckConstraint("days > 0", name="days_pos"),
        CheckConstraint("label <> ''", name="label_not_empty"),
        CheckConstraint("multiplier > 1", name="multiplier_gt_1"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    days: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    multiplier: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ============================================================
# BADGES
# ============================================================
class Badge(Base):
    __tablename__ = "badges"
    __table_args__ = (
        CheckConstraint("code <> ''", name="code_not_empty"),
        CheckConstraint("code = upper(code)", name="code_uppercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user_badges: Mapped[list["UserBadge"]] = relationship("UserBadge", back_populates="badge")


# ============================================================
# USER_CAB_BALANCE
# ============================================================
class UserCabBalance(Base):
    __tablename__ = "user_cab_balance"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # PG names the constraint ``balance_nn`` (initial migration 0001). The
    # ORM previously used ``user_cab_balance_balance_check`` — that name has
    # never matched anything in PG and is fixed here as part of Pattern A.
    __table_args__ = (CheckConstraint("balance >= 0", name="balance_nn"),)

    user: Mapped["User"] = relationship("User", back_populates="cab_balance")


# ============================================================
# USER_CASHBACK_BALANCE
# ============================================================
class UserCashbackBalance(Base):
    __tablename__ = "user_cashback_balance"
    __table_args__ = (
        # PG has two redundant CHECKs on the same predicate — initial schema
        # ``balance_nn`` and the auto-named ``user_cashback_balance_balance_check``.
        # Mirror both names so the schema-sync test stays clean.
        CheckConstraint("balance >= 0", name="balance_nn"),
        CheckConstraint(
            "balance >= 0",
            name="user_cashback_balance_balance_check",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="cashback_balance")


# ============================================================
# CABECOIN_TRANSACTIONS
# ============================================================
_CAB_REASONS = (
    "receipt_scan",
    "label_scan",
    # Phase B (PR #325) renamed barcode_scan → product_identification ;
    # the legacy reason stays accepted so historical rows persist.
    "barcode_scan",
    "product_identification",
    "fill_product_field",  # Phase B — manual product attribute filling.
    "scan_distinct",  # Phase B — diversity badge / mission progress.
    "promo_found",  # Phase B — user-flagged in-store promo.
    "mission_reward",
    "battlepass_milestone",
    "referral",
    "cashback_boost_debit",
    "cashback_boost_refund",
    "shop_purchase",
    "stonks_boost",
    "mission_freeze",
    "food_reserve_purchase",  # Feed Jack — keep in sync with VALID_REASONS / DA-04
    "streak_repair",  # Feed Jack — keep in sync with VALID_REASONS / DA-04
    "challenge_milestone",  # Défi communautaire — keep in sync with VALID_REASONS
    "mystery_product",  # Produit mystère — keep in sync with VALID_REASONS / DA-04
    "admin_adjustment",  # Admin manual mutation — keep in sync with VALID_REASONS / KP-08
    "retro_scan",  # ratis_batch_data_reconciliation Job 4 — retroactive CAB on
    # scans newly resolved by Job 1 (Bloc I NRC). Isolated from
    # the financial batch via reference_type='retro_scan' too.
    "gift_card_purchase",  # Boutique V1 debit — keep in sync with VALID_REASONS / KP-08.
    # Distinct from the legacy 'shop_purchase' reason kept for
    # historical rows + the gift_card_orders.source_type enum.
    "gift_card_refund",  # Boutique V1 — CAB refunded when gift-card issuance fails.
    # Keep in sync with VALID_REASONS / KP-08.
    "achievement_unlock",  # Achievements V1 — CAB granted on unlock (catalog `cab_reward`).
    # Keep in sync with VALID_REASONS / KP-08.
)


class CabecoinsTransaction(Base):
    __tablename__ = "cabecoin_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reference_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint("direction IN ('credit', 'debit')", name="cabecoin_transactions_direction_check"),
        CheckConstraint("amount > 0", name="cabecoin_transactions_amount_check"),
        CheckConstraint(
            f"reason IN ({', '.join(repr(r) for r in _CAB_REASONS)})",
            name="cabecoin_transactions_reason_check",
        ),
        CheckConstraint(
            "(reference_id IS NULL) = (reference_type IS NULL)",
            name="cabecoin_transactions_reference_consistency_check",
        ),
        CheckConstraint(
            "reference_type IS NULL OR reference_type IN "
            "('scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
            "'community_challenge_milestone', 'admin', 'retro_scan', 'achievement')",
            name="cabecoin_transactions_reference_type_check",
        ),
        Index(
            "uq_cabtx_scan_credit",
            "reference_id",
            unique=True,
            postgresql_where=sa_text("direction = 'credit' AND reference_type = 'scan'"),
        ),
        # Idempotence guard for ratis_batch_data_reconciliation Job 4.
        # A rerun after a crash cannot double-credit the same scan via
        # reference_type='retro_scan'.
        Index(
            "uq_cabtx_retro_scan_credit",
            "reference_id",
            unique=True,
            postgresql_where=sa_text("direction = 'credit' AND reference_type = 'retro_scan'"),
        ),
    )

    user: Mapped["User | None"] = relationship("User", back_populates="cabecoin_transactions")


# ============================================================
# REWARD_EVENTS  — Phase B audit + idempotency table
# ============================================================
class RewardEvent(Base):
    """Append-only ledger of every gamification event.

    Each row records a single ``trigger_action`` call : action_type,
    optional qualifier, quantity, the caller-provided idempotency_key
    (or one synthesised by the server), and a free JSONB ``payload``
    that carries the originating context (scan_id, ean, store_id…).

    The unique constraint on ``idempotency_key`` is the deduplication
    primitive : ``ON CONFLICT DO NOTHING`` lets the second writer fall
    through without re-awarding CAB / XP / mission progress.

    ``status`` lifecycle :
        pending   — row inserted, side-effects in flight (transient).
        processed — CAB/XP/missions awarded.
        duplicate — collided with an existing idempotency_key.
        failed    — side-effects raised ; payload['error'] holds the message.
    """

    __tablename__ = "reward_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    qualifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("quantity > 0", name="reward_events_quantity_positive"),
        CheckConstraint(
            "status IN ('pending', 'processed', 'duplicate', 'failed')",
            name="reward_events_status_check",
        ),
        UniqueConstraint("idempotency_key", name="uq_reward_events_idempotency_key"),
        Index(
            "ix_reward_events_user_action",
            "user_id",
            "action_type",
            "created_at",
            postgresql_using="btree",
        ),
    )


# ============================================================
# BATTLEPASS_SEASONS
# ============================================================
class BattlepassSeason(Base):
    __tablename__ = "battlepass_seasons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    milestones: Mapped[list["BattlepassMilestone"]] = relationship("BattlepassMilestone", back_populates="season")
    progresses: Mapped[list["UserBattlepassProgress"]] = relationship("UserBattlepassProgress", back_populates="season")


# ============================================================
# BATTLEPASS_MILESTONES
# ============================================================
class BattlepassMilestone(Base):
    __tablename__ = "battlepass_milestones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    season_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("battlepass_seasons.id", ondelete="RESTRICT"), nullable=False
    )
    milestone_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cab_required: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_type: Mapped[str] = mapped_column(Text, nullable=False)
    reward_value: Mapped[int] = mapped_column(Integer, nullable=False)
    subscriber_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("season_id", "milestone_number"),
        CheckConstraint(
            "reward_type IN ('cab', 'gift_card', 'skin')",
            name="battlepass_milestones_reward_type_check",
        ),
    )

    season: Mapped["BattlepassSeason"] = relationship("BattlepassSeason", back_populates="milestones")
    claims: Mapped[list["UserBattlepassClaim"]] = relationship("UserBattlepassClaim", back_populates="milestone")


# ============================================================
# USER_BATTLEPASS_PROGRESS
# ============================================================
class UserBattlepassProgress(Base):
    __tablename__ = "user_battlepass_progress"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    season_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("battlepass_seasons.id", ondelete="RESTRICT"), nullable=False
    )
    cab_earned_season: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "season_id"),)

    season: Mapped["BattlepassSeason"] = relationship("BattlepassSeason", back_populates="progresses")


# ============================================================
# USER_BATTLEPASS_CLAIMS
# ============================================================
class UserBattlepassClaim(Base):
    __tablename__ = "user_battlepass_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("battlepass_milestones.id", ondelete="RESTRICT"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "milestone_id"),)

    milestone: Mapped["BattlepassMilestone"] = relationship("BattlepassMilestone", back_populates="claims")


# ============================================================
# MISSIONS
# ============================================================
class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    # qualifier : optional filter on the action (e.g. "organic", "french",
    # "category", "store"). NULL = no filter (V0 behaviour). Phase B service
    # code interprets the qualifier per action_type.
    qualifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cab_reward: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_boostable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        # Unicity now keyed on the qualifier too — same (action_type,
        # frequency, difficulty) tuple may exist with different qualifiers.
        # ``postgresql_nulls_not_distinct=True`` (PG 15+) treats NULL
        # qualifiers as a real value, so duplicates with qualifier=NULL
        # are rejected. Default PG semantics would let them through.
        UniqueConstraint(
            "action_type",
            "qualifier",
            "frequency",
            "difficulty",
            name="uq_mission",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            # ``barcode_scan`` is the V0 name kept admitted alongside its
            # phase B rename (``product_identification``) so historical
            # rows survive the migration. The seed never emits
            # ``barcode_scan`` after phase B (PR #325) — the runtime
            # always uses the new name — but tests and migrations need
            # the legacy value accepted.
            "action_type IN ('receipt_scan', 'label_scan', "
            "'barcode_scan', 'product_identification', 'price_compared', "
            "'fill_product_field', 'scan_distinct', 'promo_found')",
            name="missions_action_type_check",
        ),
        CheckConstraint(
            "frequency IN ('daily', 'weekly')",
            name="missions_frequency_check",
        ),
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="missions_difficulty_check",
        ),
    )

    user_missions: Mapped[list["UserMission"]] = relationship("UserMission", back_populates="mission")


# ============================================================
# USER_MISSIONS
# ============================================================
class UserMission(Base):
    __tablename__ = "user_missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="RESTRICT"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Buffer + Burst (PR Buffer/Burst V1, refonte Stonks 2026-05-09).
    # ``buffer_count`` was renamed from ``boost_count`` in the same migration ;
    # see docs/superpowers/specs/2026-05-09-buffer-burst-design.md.
    buffer_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    burst_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    period_extended_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    burst_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    portions_claimed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cab_reward: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    xp_reward: Mapped[Decimal] = mapped_column(Numeric(), nullable=False, server_default="0")
    frozen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    freeze_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # tracked_values : bag of distinct values observed during the period.
    # Used by ``scan_distinct`` action_type (mission counts unique values
    # rather than total events). NULL for every other action_type.
    tracked_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "mission_id", "period_start"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'claimed')",
            name="user_missions_status_check",
        ),
    )

    mission: Mapped["Mission"] = relationship("Mission", back_populates="user_missions")


# ============================================================
# USER_STREAKS  — Feed Jack streak (DA-09 / DA-10 / DA-11)
# ============================================================
class UserStreak(Base):
    __tablename__ = "user_streaks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Feed Jack streak state
    current_streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_fed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    food_reserves: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Europe/Paris")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("current_streak_days >= 0", name="user_streaks_streak_days_nn"),
        CheckConstraint("food_reserves >= 0", name="user_streaks_food_reserves_nn"),
    )

    user: Mapped["User"] = relationship("User", back_populates="streaks")


# ============================================================
# USER_BADGES
# ============================================================
class UserBadge(Base):
    __tablename__ = "user_badges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    badge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("badges.id", ondelete="CASCADE"), nullable=False
    )
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "badge_id"),)

    user: Mapped["User"] = relationship("User", back_populates="badges")
    badge: Mapped["Badge"] = relationship("Badge", back_populates="user_badges")


# ============================================================
# LEADERBOARD_SNAPSHOTS
# ============================================================
class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    cab_earned: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "period_year", "period_month"),
        CheckConstraint("cab_earned >= 0", name="cab_earned_nn"),
        CheckConstraint(
            "period_month >= 1 AND period_month <= 12",
            name="month_range",
        ),
        CheckConstraint("rank > 0", name="rank_pos"),
        CheckConstraint(
            "period_year >= 2024 AND period_year <= 2100",
            name="year_range",
        ),
    )

    user: Mapped["User"] = relationship("User", back_populates="leaderboard_snapshots")


# ============================================================
# USER_XP_BALANCE
# ============================================================
class UserXpBalance(Base):
    __tablename__ = "user_xp_balance"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(), nullable=False, server_default="0")
    level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (CheckConstraint("balance >= 0", name="user_xp_balance_positive"),)

    user: Mapped["User"] = relationship("User", back_populates="xp_balance")


# ============================================================
# XP_TRANSACTIONS
# ============================================================
_XP_REASONS = (
    "receipt_scan",
    "label_scan",
    # Phase B (PR #325) renamed barcode_scan → product_identification ;
    # the legacy reason stays accepted so historical XP rows persist.
    "barcode_scan",
    "product_identification",
    "fill_product_field",  # Phase B — manual product attribute filling.
    "scan_distinct",  # Phase B — diversity badge / mission progress.
    "promo_found",  # Phase B — user-flagged in-store promo.
    "price_compared",
    "mission_completed",
    "battlepass_milestone",
    "referral",
    "feed_jack",
    # Legacy reason kept accepted for historical XP rows minted before
    # the Buffer + Burst refonte (2026-05-09). The runtime now emits
    # 'mission_burst' for Burst palier claims.
    "stonks_completion",
    "challenge_milestone",  # Défi communautaire — keep in sync with VALID_XP_REASONS
    "mission_burst",  # Buffer + Burst — Burst palier claim XP credit.
)


class XpTransaction(Base):
    __tablename__ = "xp_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reference_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            f"reason IN ({', '.join(repr(r) for r in _XP_REASONS)})",
            name="xp_reason_check",
        ),
        CheckConstraint("amount > 0", name="xp_amount_positive"),
        # reference_type allowlist — mirrors the live
        # ``cabecoin_transactions_reference_type_check`` literal set so the
        # two parallel ledger tables stay schema-aligned (audit RW-04 —
        # xp_transactions previously had NO such CHECK). Any literal added
        # to either CHECK must be added here (KP-08 multi-place sync).
        CheckConstraint(
            "reference_type IS NULL OR reference_type IN "
            "('scan', 'mission', 'battlepass_milestone', 'referral', 'user_mission', "
            "'community_challenge_milestone', 'admin', 'retro_scan', 'achievement')",
            name="xp_transactions_reference_type_check",
        ),
        # Reference consistency — reference_id and reference_type are set
        # together or both NULL. Mirrors
        # ``cabecoin_transactions_reference_consistency_check``.
        CheckConstraint(
            "(reference_id IS NULL) = (reference_type IS NULL)",
            name="xp_transactions_reference_consistency_check",
        ),
    )

    # No back-populates on User — RGPD anonymize drops the FK to users.id
    # which would orphan SQLAlchemy's relationship loader. Cf F-AU-3.


# ============================================================
# MISSION_XP_RECORDS — Burst leaderboard table
# ============================================================
# Replaces stonks_records (= dropped 2026-05-09 by refonte Buffer + Burst).
# 1 record per (user, user_mission) — captures the max XP earned via Burst
# paliers on that mission. Indexed for monthly + all-time leaderboard
# queries. See docs/superpowers/specs/2026-05-09-buffer-burst-design.md.
class MissionXpRecord(Base):
    __tablename__ = "mission_xp_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    xp_earned: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    burst_count: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_mission_id", name="uq_mxr_user_mission"),
        CheckConstraint("xp_earned > 0", name="mxr_xp_earned_positive"),
        CheckConstraint("burst_count >= 0", name="mxr_burst_count_nn"),
        CheckConstraint("buffer_count >= 0", name="mxr_buffer_count_nn"),
    )

    # No back-populates on User — RGPD anonymize drops the FK to users.id.
    mission: Mapped["Mission"] = relationship("Mission")


# ============================================================
# COMMUNITY_CHALLENGES
# ============================================================
class CommunityChallenge(Base):
    __tablename__ = "community_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    action_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    objective: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    grace_period_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    milestones: Mapped[list["CommunityChallengeMilestone"]] = relationship(
        "CommunityChallengeMilestone", back_populates="challenge"
    )
    progress: Mapped["CommunityChallengeProgress | None"] = relationship(
        "CommunityChallengeProgress", back_populates="challenge", uselist=False
    )

    __table_args__ = (
        # Enforce at most one active challenge at a time.
        # create_all creates this index in the test DB so IntegrityError-based
        # conflict detection works without relying on the Alembic migration.
        Index(
            "community_challenges_one_active",
            "is_active",
            unique=True,
            postgresql_where=sa_text("is_active = TRUE"),
        ),
    )


# ============================================================
# COMMUNITY_CHALLENGE_MILESTONES
# ============================================================
class CommunityChallengeMilestone(Base):
    __tablename__ = "community_challenge_milestones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_challenges.id", ondelete="CASCADE"),
        nullable=False,
    )
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_type: Mapped[str] = mapped_column(Text, nullable=False)
    reward_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "reward_type IN ('cab', 'xp', 'skin', 'multiplier')",
            name="community_challenge_milestones_reward_type_check",
        ),
    )

    challenge: Mapped["CommunityChallenge"] = relationship("CommunityChallenge", back_populates="milestones")
    claims: Mapped[list["CommunityChallengeClaim"]] = relationship(
        "CommunityChallengeClaim", back_populates="milestone"
    )


# ============================================================
# COMMUNITY_CHALLENGE_PROGRESS  (one row per challenge)
# ============================================================
class CommunityChallengeProgress(Base):
    __tablename__ = "community_challenge_progress"

    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_challenges.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "current_count >= 0",
            name="community_challenge_progress_count_nn",
        ),
    )

    challenge: Mapped["CommunityChallenge"] = relationship("CommunityChallenge", back_populates="progress")


# ============================================================
# COMMUNITY_CHALLENGE_CLAIMS
# ============================================================
class CommunityChallengeClaim(Base):
    __tablename__ = "community_challenge_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_challenges.id", ondelete="CASCADE"),
        nullable=False,
    )
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_challenge_milestones.id", ondelete="CASCADE"),
        nullable=False,
    )
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("milestone_id", "user_id", name="uq_challenge_claims_milestone_user"),)

    milestone: Mapped["CommunityChallengeMilestone"] = relationship(
        "CommunityChallengeMilestone", back_populates="claims"
    )


# ============================================================
# COMMUNITY_MULTIPLIERS
# ============================================================
class CommunityMultiplier(Base):
    __tablename__ = "community_multipliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_challenges.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    multiplier: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    applies_to: Mapped[str] = mapped_column(Text, nullable=False)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_community_multipliers_challenge_user"),
        CheckConstraint(
            "applies_to IN ('cab', 'xp', 'both')",
            name="community_multipliers_applies_to_check",
        ),
    )


# ============================================================
# USER_SAVINGS_SNAPSHOT — hybrid snapshot for total savings + ROI rings
# ============================================================
class UserSavingsSnapshot(Base):
    __tablename__ = "user_savings_snapshot"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lifetime_savings_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    rings_consumed: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    last_computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "lifetime_savings_cents >= 0",
            name="ck_user_savings_snapshot_lifetime_nonneg",
        ),
        CheckConstraint(
            "rings_consumed >= 0",
            name="ck_user_savings_snapshot_rings_nonneg",
        ),
    )
