from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.user import User


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("code = upper(code)", name="referral_codes_code_upper_check"),
        CheckConstraint("type IN ('user', 'influencer')", name="referral_codes_type_check"),
    )

    user: Mapped["User | None"] = relationship("User", back_populates="referral_code")
    uses: Mapped[list["ReferralUse"]] = relationship("ReferralUse", back_populates="referral_code")


class ReferralUse(Base):
    __tablename__ = "referral_uses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    referral_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("referral_codes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    referred_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        unique=True,
    )
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (CheckConstraint("plan IN ('monthly', 'annual')", name="referral_uses_plan_check"),)

    referral_code: Mapped["ReferralCode"] = relationship("ReferralCode", back_populates="uses")
    # No referred_user relationship — FK to users.id dropped by RGPD anonymize
    # (F-AU-3). Query ReferralUse.referred_user_id directly.
