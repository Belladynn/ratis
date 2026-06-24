"""Subscription service — Stripe Checkout integration."""

import contextlib
import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import sentry_sdk
import stripe
from dateutil.relativedelta import relativedelta
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

import repositories.subscription_repository as sub_repo
from ratis_core.models.referral import ReferralUse
from ratis_core.models.rewards import DiscountCampaign, StripeWebhookEvent, Subscription
from ratis_core.settings import load_settings


def get_subscription_id_from_metadata(session_obj) -> str | None:
    """Extract subscription_id from Stripe session metadata."""
    return (session_obj.get("metadata") or {}).get("subscription_id")


def _ensure_stripe_key() -> None:
    if not stripe.api_key:
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]


_settings = None


def _get_settings() -> dict:
    global _settings
    if _settings is None:
        cfg = load_settings()
        if "subscription" not in cfg:
            raise KeyError("Settings missing 'subscription' section — check app_settings table or ratis_settings.json")
        _settings = cfg
    return _settings["subscription"]


# ============================================================
# Discount validation
# ============================================================


def _validate_discount(campaign: DiscountCampaign) -> None:
    """Raise ValueError with specific error code if discount is unusable."""
    if not campaign.is_public:
        raise ValueError("discount_code_invalid")
    now = datetime.now(UTC)
    if campaign.valid_from and campaign.valid_from > now:
        raise ValueError("discount_code_expired")
    if campaign.valid_until and campaign.valid_until < now:
        raise ValueError("discount_code_expired")
    if campaign.max_uses is not None and campaign.uses_count >= campaign.max_uses:
        raise ValueError("discount_code_exhausted")


def _compute_discount_amount(catalog_price: Decimal, campaign: DiscountCampaign) -> Decimal:
    """Return the discount amount (positive) to subtract from catalog_price."""
    if campaign.type == "percentage":
        amount = (catalog_price * campaign.value / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:  # fixed
        amount = campaign.value
    # Discount must never equal or exceed price (DB constraint discount_not_exceed_price)
    return min(amount, catalog_price - Decimal("0.01"))


# ============================================================
# POST /account/subscription
# ============================================================


def create_checkout(
    db: Session,
    user_id: uuid.UUID,
    plan: str,
    discount_campaign_code: str | None,
    currency: str = "eur",
) -> str:
    """Create a Stripe Checkout Session and INSERT a pending subscription. Returns checkout_url."""
    _ensure_stripe_key()
    if sub_repo.get_active(db, user_id):
        raise ValueError("already_subscribed")
    cfg = _get_settings()

    catalog_price = Decimal(str(cfg["price_monthly_eur"] if plan == "monthly" else cfg["price_annual_eur"]))
    if catalog_price <= 0:
        raise ValueError("invalid_catalog_price")
    discount_amount: Decimal | None = None

    # Validate discount if provided
    campaign: DiscountCampaign | None = None
    if discount_campaign_code:
        campaign = sub_repo.get_discount_campaign(db, discount_campaign_code)
        if not campaign:
            raise ValueError("discount_code_invalid")
        _validate_discount(campaign)
        discount_amount = _compute_discount_amount(catalog_price, campaign)

    final_price = catalog_price - discount_amount if discount_amount else catalog_price
    unit_amount_cents = int((final_price * 100).to_integral_value())

    # Placeholder expires_at for the pending row (overwritten by webhook)
    placeholder_expires = datetime.now(UTC) + (relativedelta(months=1) if plan == "monthly" else relativedelta(years=1))

    # Generate the subscription id up front so it can be attached to the Stripe
    # session metadata at creation time. The webhook links the payment back to
    # this row via metadata["subscription_id"] — attaching it directly to
    # Session.create (rather than a separate Session.modify after the fact)
    # eliminates the window where a created session has no metadata: if the
    # webhook fired for such a session, a paid customer would have no
    # subscription. Session id == Subscription id (uuid.uuid4, client-side
    # default) so it is fully known before the Stripe call.
    subscription_id = uuid.uuid4()

    # Build Stripe session.
    # NOTE: `currency_conversion` (Stripe Adaptive Pricing) is a valid live
    # Checkout param, but the bundled stripe type stubs lag the API and omit it.
    # Runtime is correct; the stub is incomplete — hence the scoped ignore below.
    session = stripe.checkout.Session.create(  # type: ignore[call-arg]
        mode="payment",
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": currency,
                    "unit_amount": unit_amount_cents,
                    "product_data": {
                        "name": f"Ratis Premium — {'Mensuel' if plan == 'monthly' else 'Annuel'}",
                    },
                },
                "quantity": 1,
            }
        ],
        currency_conversion={"enabled": True},
        success_url=cfg["success_url"],
        cancel_url=cfg["cancel_url"],
        metadata={"subscription_id": str(subscription_id)},
    )

    sub_repo.create_pending(
        db,
        subscription_id=subscription_id,
        user_id=user_id,
        plan=plan,
        stripe_session_id=session.id,
        price=catalog_price,
        discount_campaign_code=discount_campaign_code if campaign else None,
        discount_amount=discount_amount,
        placeholder_expires_at=placeholder_expires,
    )
    db.commit()

    return session.url


# ============================================================
# GET /account/subscription
# ============================================================


def get_active(db: Session, user_id: uuid.UUID) -> Subscription | None:
    return sub_repo.get_active(db, user_id)


# ============================================================
# DELETE /account/subscription
# ============================================================


def cancel_active(db: Session, user_id: uuid.UUID) -> None:
    """Cancel the active subscription via Stripe + mark cancelled in DB."""
    _ensure_stripe_key()
    sub = sub_repo.get_active(db, user_id)
    if not sub:
        raise LookupError("no_active_subscription")

    # Cancel on Stripe if payment_ref exists — already cancelled or uncancellable is fine
    if sub.payment_ref:
        with contextlib.suppress(stripe._error.InvalidRequestError):
            stripe.PaymentIntent.cancel(sub.payment_ref)

    sub_repo.cancel(db, sub)
    db.commit()


# ============================================================
# Webhook: checkout.session.completed
# ============================================================


def handle_checkout_completed(db: Session, session_obj) -> None:
    """Activate subscription and update referral_use.plan if applicable."""
    subscription_id = get_subscription_id_from_metadata(session_obj)
    if not subscription_id:
        return

    sub = db.query(Subscription).filter(Subscription.id == uuid.UUID(subscription_id)).first()
    if not sub or sub.status != "pending":
        return

    now = datetime.now(UTC)
    expires_at = now + (relativedelta(months=1) if sub.plan == "monthly" else relativedelta(years=1))

    payment_ref = session_obj.get("payment_intent", "")
    amount_total = session_obj.get("amount_total", 0)
    currency = session_obj.get("currency", "eur")

    # Cross-check the amount paid against the price recorded when the pending
    # subscription was created. ``sub.price`` is the catalog price and
    # ``sub.discount_amount`` the validated discount — their difference is the
    # amount Stripe Checkout was asked to charge. An underpayment means the
    # webhook payload diverges from what we billed (tampering or a Stripe
    # config drift) — flag it loudly, but do not block activation since the
    # payment did succeed on Stripe's side.
    expected = sub.price - (sub.discount_amount or Decimal("0"))
    expected_cents = int((expected * 100).to_integral_value())
    if amount_total < expected_cents:
        log.warning(
            "stripe_amount_mismatch: subscription %s paid %s cents, expected >= %s cents",
            sub.id,
            amount_total,
            expected_cents,
        )
        sentry_sdk.capture_message(
            f"Stripe checkout underpayment for subscription {sub.id}: "
            f"paid {amount_total} cents, expected {expected_cents} cents",
            level="warning",
        )

    sub_repo.activate(
        db,
        sub,
        payment_ref=payment_ref,
        started_at=now,
        expires_at=expires_at,
        amount_total_cents=amount_total,
        currency=currency,
    )

    # discount_campaigns.uses_count is incremented by fn_increment_discount_uses trigger
    # which fires on INSERT OR UPDATE when status transitions to 'active'.
    # No Python increment needed — the trigger handles it atomically.

    # Update referral_use.plan if this user was referred and plan is still NULL
    referral_use = (
        db.query(ReferralUse).filter(ReferralUse.referred_user_id == sub.user_id, ReferralUse.plan.is_(None)).first()
    )
    if referral_use:
        referral_use.plan = sub.plan

    db.commit()


# ============================================================
# Webhook: checkout.session.expired
# ============================================================


def handle_checkout_expired(db: Session, session_obj) -> None:
    """Cancel pending subscription when the Stripe Checkout Session expires.

    checkout.session.expired fires with a CheckoutSession object whose id = cs_...
    This is the correct event to use — payment_intent.payment_failed fires with a
    PaymentIntent object (id = pi_...) which does not match stripe_session_id.
    """
    stripe_session_id = session_obj["id"]
    sub_repo.cancel_by_stripe_session(db, stripe_session_id)
    db.commit()


# ============================================================
# Webhook idempotency (audit C2)
# ============================================================


def claim_stripe_event(db: Session, event_id: str, event_type: str) -> bool:
    """Atomically claim a Stripe webhook event_id for idempotency.

    Returns True if this is the FIRST receipt of ``event_id`` (the caller
    must process the event), False if it was already received (the caller
    must short-circuit). Uses INSERT ... ON CONFLICT DO NOTHING with
    RETURNING id so concurrent retries race safely on the UNIQUE(event_id)
    constraint.

    Note: ``rowcount`` is unreliable for INSERT … ON CONFLICT DO NOTHING
    within nested transactions / SAVEPOINTs (psycopg3 returns -1). We use
    RETURNING id instead — an inserted row returns its UUID; a no-op
    conflict returns nothing.
    """
    stmt = (
        pg_insert(StripeWebhookEvent)
        .values(event_id=event_id, event_type=event_type)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(StripeWebhookEvent.id)
    )
    result = db.execute(stmt)
    inserted = result.fetchone() is not None
    db.commit()
    return inserted
