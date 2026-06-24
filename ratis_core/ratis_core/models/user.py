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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base
from ratis_core.identifiers import generate_support_id

if TYPE_CHECKING:
    # Note : XpTransaction, MissionXpRecord, LabelSession, ReferralUse were
    # removed by the RGPD anonymize completeness fix (audit F-AU-3,
    # 2026-05-11). FK to users.id was dropped on those tables so the SQLAlchemy
    # back-populates relationships are no longer declarable. Query the
    # tables directly if needed.
    from ratis_core.models.analytics import (
        NotificationLog,
        PriceChallengeResponse,
        UserPreferences,
        UserPushToken,
        UserSession,
        UserSessionStat,
    )
    from ratis_core.models.gamification import (
        CabecoinsTransaction,
        LeaderboardSnapshot,
        LevelTier,
        UserBadge,
        UserCabBalance,
        UserCashbackBalance,
        UserStreak,
        UserXpBalance,
    )
    from ratis_core.models.referral import ReferralCode
    from ratis_core.models.rewards import CashbackTransaction, CashbackWithdrawal, GiftCardOrder, Subscription
    from ratis_core.models.scan import Receipt, Scan
    from ratis_core.models.shopping import (
        OptimizedRoute,
        PriceAlert,
        ProductTracking,
        ShoppingList,
        UserStorePreference,
    )


# ============================================================
# USERS
# ============================================================
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # ``email`` is NOT unique since H2 Phase 2 (migration
    # ``20260518_1300_acct_type``). The account key moved to
    # ``user_identities.(provider, provider_id)`` — two accounts (one
    # Apple, one Google) may legitimately share an email. ``email`` is a
    # purely informative contact field. It stays ``NOT NULL``.
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # Public, non-PII support identifier of shape ``RTS-XXXXXX``.
    # Production path : :func:`webservices.ratis_auth.repositories.user_repository.create_user`
    # generates one via :func:`ratis_core.identifiers.generate_support_id`
    # with retry on UNIQUE violation. The model-level ``default`` covers
    # direct ``User(...)`` instantiations (tests, scripts) without
    # collision retry — acceptable because the keyspace (32^6 ≈ 1B) makes
    # collisions astronomically rare for one-shot test seeds. See
    # :mod:`ratis_core.identifiers` for the format contract.
    # Uniqueness is enforced in PG by the standalone UNIQUE INDEX
    # ``uq_users_support_id`` created in migration ``20260501_1500_supid``
    # (via ``op.create_index(..., unique=True)``). It is declared here as an
    # ``Index`` in ``__table_args__`` — NOT as ``unique=True`` on the column —
    # so the ORM models a unique *index* and not a ``UniqueConstraint``.
    # ``unique=True`` would make autogenerate diff a constraint against the
    # PG index and report a spurious ``add_constraint`` (cf alembic check).
    support_id: Mapped[str] = mapped_column(Text, nullable=False, default=generate_support_id)
    # ``account_type`` — narrowed account *state*, not an OAuth provider.
    # The real OAuth identity lives in ``user_identities`` since H2 Phase 2
    # (migration ``20260518_1300_acct_type``). Accepted values :
    #   * ``oauth``    : a normal user — identities are in ``user_identities``.
    #   * ``internal`` : admin / sentinel rows (no identity).
    #   * ``deleted``  : RGPD tombstone written by ``delete_account``.
    #   * ``dev``      : seed-only marker for the 6 ``scripts/seed/`` personas.
    account_type: Mapped[str] = mapped_column(Text, nullable=False, default="oauth")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Europe/Paris", server_default="Europe/Paris")
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_level_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("level_tiers.id", ondelete="SET NULL"), nullable=True
    )
    ref_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 3), nullable=True)
    ref_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 3), nullable=True)

    # ── Anti-fraud V1 (NRC trust score) ───────────────────────────
    # See ARCH_anti_fraud.md for the full contract. trust_score is
    # batch-recomputed nightly by ``ratis_batch_trust_score`` from the
    # user's contributions in ``product_name_resolutions``. The starting
    # value 50 is neutral — neither clean nor suspect.
    trust_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    # Denormalised count of contributing rows on labels that reached a
    # consensus state (VERIFIED / UNVERIFIED). Drives the 100-scan grace
    # period gate — sanctions kick in only at >= 100.
    total_resolved_scans: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Automatic flag — flipped to True by the batch when trust_score < 65
    # AND total_resolved_scans >= 100. Effects : weight_override = 0 on
    # all future ledger writes ; CAB scan rewards skipped silently. Admin
    # may flip back via PATCH /admin/users/{id}/shadow-ban.
    is_shadow_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    trust_score_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Denormalised cumulative gift-card redemption (cents) used by the
    # boutique to enforce the 1199 €/an DAS2 fiscal cap. Reset every
    # 1st of January by the annual purge batch (cf ARCH_cab_economy
    # § Plafond annuel). Boutique POSTs UPDATE this column atomically.
    gift_card_redeemed_ytd_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        # Mirrors the PG standalone UNIQUE INDEX from migration
        # ``20260501_1500_supid``. Modelled as an ``Index`` (not a
        # ``UniqueConstraint``) so autogenerate stays drift-free.
        Index("uq_users_support_id", "support_id", unique=True),
        CheckConstraint(
            "trust_score >= 0 AND trust_score <= 100",
            name="users_trust_score_range_chk",
        ),
        CheckConstraint(
            r"email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'",
            name="email_format",
        ),
        # ``account_type_check`` — whitelist of accepted ``users.account_type``
        # values. Since H2 Phase 2 (migration ``20260518_1300_acct_type``)
        # the column holds account *states*, not OAuth providers : the
        # OAuth identity moved to ``user_identities``. ``'deleted'`` is the
        # tombstone state written by ``delete_account`` ; ``'dev'`` is the
        # seed-only marker for the 6 ``scripts/seed/`` personas.
        # Mirrored here so ``Base.metadata.create_all`` test bootstraps
        # reject the same invalid rows production would (Pattern A — see
        # ``ratis_core/tests/test_schema_sync.py``).
        CheckConstraint(
            "account_type IN ('oauth', 'internal', 'deleted', 'dev')",
            name="account_type_check",
        ),
    )

    current_level: Mapped["LevelTier | None"] = relationship("LevelTier", back_populates="users")
    receipts: Mapped[list["Receipt"]] = relationship("Receipt", back_populates="user")
    scans: Mapped[list["Scan"]] = relationship("Scan", back_populates="user")
    # Note : label_sessions relationship removed by RGPD anonymize completeness
    # fix (audit F-AU-3, 2026-05-11). FK to users.id dropped on label_sessions.
    # Query directly via db.query(LabelSession).filter_by(user_id=user.id) if
    # needed.
    shopping_lists: Mapped[list["ShoppingList"]] = relationship("ShoppingList", back_populates="user")
    product_tracking: Mapped[list["ProductTracking"]] = relationship("ProductTracking", back_populates="user")
    push_tokens: Mapped[list["UserPushToken"]] = relationship("UserPushToken", back_populates="user")
    preferences: Mapped["UserPreferences | None"] = relationship(
        "UserPreferences", back_populates="user", uselist=False
    )
    optimized_routes: Mapped[list["OptimizedRoute"]] = relationship("OptimizedRoute", back_populates="user")
    cab_balance: Mapped["UserCabBalance | None"] = relationship("UserCabBalance", back_populates="user", uselist=False)
    cashback_balance: Mapped["UserCashbackBalance | None"] = relationship(
        "UserCashbackBalance", back_populates="user", uselist=False
    )
    cabecoin_transactions: Mapped[list["CabecoinsTransaction"]] = relationship(
        "CabecoinsTransaction", back_populates="user"
    )
    cashback_transactions: Mapped[list["CashbackTransaction"]] = relationship(
        "CashbackTransaction", back_populates="user"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="user")
    streaks: Mapped["UserStreak | None"] = relationship("UserStreak", back_populates="user", uselist=False)
    badges: Mapped[list["UserBadge"]] = relationship("UserBadge", back_populates="user")
    leaderboard_snapshots: Mapped[list["LeaderboardSnapshot"]] = relationship(
        "LeaderboardSnapshot", back_populates="user"
    )
    price_challenge_responses: Mapped[list["PriceChallengeResponse"]] = relationship(
        "PriceChallengeResponse", back_populates="user"
    )
    price_alerts: Mapped[list["PriceAlert"]] = relationship("PriceAlert", back_populates="user")
    cashback_withdrawals: Mapped[list["CashbackWithdrawal"]] = relationship("CashbackWithdrawal", back_populates="user")
    gift_card_orders: Mapped[list["GiftCardOrder"]] = relationship("GiftCardOrder", back_populates="user")
    notification_logs: Mapped[list["NotificationLog"]] = relationship("NotificationLog", back_populates="user")
    store_preferences: Mapped[list["UserStorePreference"]] = relationship("UserStorePreference", back_populates="user")
    sessions: Mapped[list["UserSession"]] = relationship("UserSession", back_populates="user")
    session_stats: Mapped[list["UserSessionStat"]] = relationship("UserSessionStat", back_populates="user")
    referral_code: Mapped["ReferralCode | None"] = relationship("ReferralCode", back_populates="user", uselist=False)
    identities: Mapped[list["UserIdentity"]] = relationship(
        "UserIdentity", back_populates="user", cascade="all, delete-orphan"
    )
    # Note : referral_use_as_referred relationship removed by RGPD anonymize
    # completeness fix (audit F-AU-3, 2026-05-11). FK to users.id dropped on
    # referral_uses.referred_user_id. Query directly :
    # db.query(ReferralUse).filter_by(referred_user_id=user.id).first()
    xp_balance: Mapped["UserXpBalance | None"] = relationship("UserXpBalance", back_populates="user", uselist=False)
    # Note : xp_transactions and mission_xp_records relationships were removed
    # by the RGPD anonymize completeness fix (audit F-AU-3, 2026-05-11). The
    # FK to users.id was dropped on those tables to allow per-user anon UUIDs
    # to live in the row, which makes the SQLAlchemy relationship unloadable.
    # No call site in the codebase used user.xp_transactions / .mission_xp_records,
    # so the relationship was removed rather than rebuilt with an explicit
    # primaryjoin + foreign(). If you need user-side aggregation in the future,
    # query the table directly :
    #     db.query(XpTransaction).filter_by(user_id=user.id).all()


# ============================================================
# USER IDENTITIES — one OAuth identity per (provider, provider_id)
# ============================================================
class UserIdentity(Base):
    __tablename__ = "user_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Informative only — the email seen at link/login time. NOT a lookup key.
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="user_identities_provider_provider_id_key"),
        CheckConstraint(
            "provider IN ('google', 'apple')",
            name="user_identities_provider_check",
        ),
    )

    user: Mapped["User"] = relationship("User", back_populates="identities")


# ============================================================
# REFRESH TOKENS — revocation table (stateful rotation)
# ============================================================
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jti: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
