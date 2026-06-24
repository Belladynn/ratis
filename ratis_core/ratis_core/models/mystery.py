from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


# ============================================================
# MYSTERY_CHALLENGES
# ============================================================
class MysteryChallenge(Base):
    __tablename__ = "mystery_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="RESTRICT"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="scheduled")
    reward_tiers: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled','active','frozen','revealed')",
            name="mystery_challenges_status_check",
        ),
        # Partial unique index: at most one active challenge at a time
        Index(
            "uq_mystery_challenges_active",
            "status",
            unique=True,
            postgresql_where=sa_text("status = 'active'"),
        ),
    )


# ============================================================
# MYSTERY_CHALLENGE_CLUES
# ============================================================
class MysteryChallengeClue(Base):
    __tablename__ = "mystery_challenge_clues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mystery_challenges.id", ondelete="CASCADE"), nullable=False
    )
    reveal_day: Mapped[int] = mapped_column(Integer, nullable=False)
    clue_text: Mapped[str] = mapped_column(Text, nullable=False)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("challenge_id", "reveal_day"),
        CheckConstraint(
            "reveal_day BETWEEN 1 AND 3",
            name="mystery_challenge_clues_reveal_day_check",
        ),
    )


# ============================================================
# MYSTERY_CHALLENGE_FINDS
# ============================================================
class MysteryChallengeFind(Base):
    __tablename__ = "mystery_challenge_finds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mystery_challenges.id", ondelete="RESTRICT"), nullable=False
    )
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    cab_awarded: Mapped[int] = mapped_column(Integer, nullable=False)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id"),
        Index("ix_mystery_challenge_finds_challenge_id", "challenge_id"),
    )


# ============================================================
# MYSTERY_CHALLENGE_EXCLUSIONS
# ============================================================
class MysteryChallengeExclusion(Base):
    __tablename__ = "mystery_challenge_exclusions"

    product_ean: Mapped[str] = mapped_column(Text, ForeignKey("products.ean", ondelete="CASCADE"), primary_key=True)
    excluded_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
