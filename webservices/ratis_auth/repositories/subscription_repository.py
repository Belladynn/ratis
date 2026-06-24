import uuid
from datetime import UTC, datetime
from decimal import Decimal

from ratis_core.models.rewards import DiscountCampaign, Subscription
from sqlalchemy.orm import Session


def get_active(db: Session, user_id: uuid.UUID) -> Subscription | None:
    return db.query(Subscription).filter(Subscription.user_id == user_id, Subscription.status == "active").first()


def get_by_stripe_session(db: Session, stripe_session_id: str) -> Subscription | None:
    return db.query(Subscription).filter(Subscription.stripe_session_id == stripe_session_id).first()


def get_discount_campaign(db: Session, code: str) -> DiscountCampaign | None:
    return db.query(DiscountCampaign).filter(DiscountCampaign.code == code).first()


def create_pending(
    db: Session,
    *,
    subscription_id: uuid.UUID,
    user_id: uuid.UUID,
    plan: str,
    stripe_session_id: str,
    price: Decimal,
    discount_campaign_code: str | None,
    discount_amount: Decimal | None,
    placeholder_expires_at: datetime,
) -> Subscription:
    sub = Subscription(
        id=subscription_id,
        user_id=user_id,
        status="pending",
        plan=plan,
        stripe_session_id=stripe_session_id,
        price=price,
        paid_with="stripe",
        discount_campaign_code=discount_campaign_code,
        discount_amount=discount_amount,
        expires_at=placeholder_expires_at,
    )
    db.add(sub)
    db.flush()
    return sub


def activate(
    db: Session,
    sub: Subscription,
    *,
    payment_ref: str,
    started_at: datetime,
    expires_at: datetime,
    amount_total_cents: int,
    currency: str,
) -> None:
    """Transition pending → active from webhook data. Uses rowcount guard for safety."""
    sub.status = "active"
    sub.payment_ref = payment_ref
    sub.started_at = started_at
    sub.expires_at = expires_at
    # Stripe amount_total is in smallest currency unit (cents for EUR)
    sub.price = Decimal(amount_total_cents) / 100
    sub.paid_with = "stripe"
    db.flush()


def cancel(db: Session, sub: Subscription) -> None:
    sub.status = "cancelled"
    sub.cancelled_at = datetime.now(UTC)
    db.flush()


def cancel_by_stripe_session(db: Session, stripe_session_id: str) -> bool:
    """Cancel a pending subscription by stripe_session_id. Returns True if found."""
    sub = get_by_stripe_session(db, stripe_session_id)
    if sub and sub.status == "pending":
        cancel(db, sub)
        return True
    return False
