"""
Referral repository (ratis_rewards side).

Queries around the *rewards* side of referrals — finding the referrer for a
given referred user, marking referral_uses as rewarded, etc. The *signup*
side (code creation + `referral_uses` row creation at register time) lives
in `webservices/ratis_auth/repositories/referral_repository.py`.

Shared DB models from `ratis_core.models.referral` :
    ReferralCode — one per user, holds the shareable code
    ReferralUse  — one per referred_user_id (X refers Y, unique on Y)
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ratis_core.models.referral import ReferralCode, ReferralUse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ReferralLookup:
    """Join-level result — carries both sides of the link needed to credit X."""

    referral_use_id: uuid.UUID
    referrer_user_id: uuid.UUID  # X — the user who shared the code
    referred_user_id: uuid.UUID  # Y — the new user who signed up with it
    plan: str | None
    rewarded_at: datetime | None


def get_for_referred_user(db: Session, referred_user_id: uuid.UUID) -> ReferralLookup | None:
    """
    Return the referral link for a given referred user, or None if none exists.

    The query joins ``referral_uses`` → ``referral_codes`` to expose the
    referrer (``referral_codes.user_id``) in a single round-trip.

    Callers :
      - ``referral_service.handle_subscription_referral`` — credits X when Y subscribes
      - ``ratis_batch_referral_payout`` — verifies Y is still subscribed before issuing gift card
    """
    stmt = (
        select(
            ReferralUse.id,
            ReferralCode.user_id,
            ReferralUse.referred_user_id,
            ReferralUse.plan,
            ReferralUse.rewarded_at,
        )
        .join(ReferralCode, ReferralUse.referral_id == ReferralCode.id)
        .where(ReferralUse.referred_user_id == referred_user_id)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None

    use_id, referrer_id, ref_user_id, plan, rewarded_at = row
    if referrer_id is None:
        # Referrer account deleted (ReferralCode.user_id is SET NULL on delete).
        # Treat as no active referral — no one to reward.
        return None

    return ReferralLookup(
        referral_use_id=use_id,
        referrer_user_id=referrer_id,
        referred_user_id=ref_user_id,
        plan=plan,
        rewarded_at=rewarded_at,
    )


def get_code_for_user(db: Session, user_id: uuid.UUID) -> ReferralCode | None:
    """Return the user's referral_codes row, or None if never generated."""
    return db.query(ReferralCode).filter(ReferralCode.user_id == user_id).first()


def create_code_for_user(db: Session, user_id: uuid.UUID) -> ReferralCode:
    """
    Lazy-create a referral code for the user. Retries up to 10 times on
    UNIQUE collision (8-char hex uppercase, 16^8 = 4.3B keyspace — collisions
    extremely rare past a few million users but we retry defensively).

    The caller commits the transaction.
    """
    for _ in range(10):
        code = secrets.token_hex(4).upper()
        try:
            nested = db.begin_nested()
            rc = ReferralCode(user_id=user_id, code=code, type="user")
            db.add(rc)
            db.flush()
            nested.commit()
            return rc
        except IntegrityError:
            nested.rollback()
    raise RuntimeError("referral_code_generation_failed")


def get_by_code(db: Session, code: str) -> ReferralCode | None:
    """Look up a referral code by its uppercase string. Admin-side use."""
    return db.query(ReferralCode).filter(ReferralCode.code == code.upper()).first()


def has_referral_use(db: Session, referred_user_id: uuid.UUID) -> bool:
    """Quick existence check : is this user already linked to a referrer?"""
    return db.query(ReferralUse).filter(ReferralUse.referred_user_id == referred_user_id).first() is not None


def create_use(
    db: Session,
    referral_id: uuid.UUID,
    referred_user_id: uuid.UUID,
) -> ReferralUse:
    """
    Create the X→Y link. Caller is responsible for :
      - Verifying no existing use for referred_user_id (else UNIQUE violation)
      - Verifying no self-parrainage (referral.user_id != referred_user_id)
      - Committing the transaction
    """
    ru = ReferralUse(referral_id=referral_id, referred_user_id=referred_user_id)
    db.add(ru)
    db.flush()
    return ru


def get_history_for_user(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """
    Return the user's referral history — one row per filleul.

    Each row exposes :
      - referred_user_display_name — None if Y has no display_name or was deleted
      - plan — 'monthly' | 'annual' | None (if not yet subscribed)
      - rewarded_at — None if not yet triggered (subscription webhook)
      - created_at — when Y signed up with the code

    Privacy / RGPD : no email, no user_id leaked. display_name is the only
    identifying field (the user can choose what they expose publicly).
    """
    from ratis_core.models.user import User

    stmt = (
        select(
            ReferralUse.id,
            ReferralUse.plan,
            ReferralUse.rewarded_at,
            ReferralUse.created_at,
            User.display_name,
            User.is_deleted,
        )
        .join(ReferralCode, ReferralUse.referral_id == ReferralCode.id)
        .outerjoin(User, User.id == ReferralUse.referred_user_id)
        .where(ReferralCode.user_id == user_id)
        .order_by(ReferralUse.created_at.desc())
    )
    out = []
    for _use_id, plan, rewarded_at, created_at, display_name, is_deleted in db.execute(stmt):
        # Hide display_name if the user was deleted (anonymisation consistent
        # with RGPD). Plan is still exposed for stats purposes.
        safe_name = None if is_deleted else display_name
        out.append(
            {
                "referred_user_display_name": safe_name,
                "plan": plan,
                "rewarded_at": rewarded_at,
                "created_at": created_at,
            }
        )
    return out


def mark_rewarded(
    db: Session,
    referral_use_id: uuid.UUID,
    plan: str,
) -> bool:
    """
    Mark a referral_uses row as rewarded (sets rewarded_at + plan).

    Idempotent : returns False if already rewarded (caller should skip the
    CAB award and gift card enqueue in that case). Returns True on success.

    The caller is responsible for committing the transaction.
    """
    use = db.get(ReferralUse, referral_use_id)
    if use is None:
        return False
    if use.rewarded_at is not None:
        return False

    use.plan = plan
    use.rewarded_at = datetime.now(tz=UTC)
    db.flush()
    return True
