"""Stripe webhook endpoint — no Bearer auth, verified via Stripe-Signature header."""

import logging
import os
from datetime import UTC

import sentry_sdk
import services.subscription_service as sub_service
import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from ratis_core.database import get_db
from ratis_core.rewards_client import trigger_annual_gift_card, trigger_referral_reward
from services.subscription_service import get_subscription_id_from_metadata
from sqlalchemy.orm import Session
from stripe._error import SignatureVerificationError as StripeSignatureError

log = logging.getLogger(__name__)
router = APIRouter()


def _webhook_secret() -> str:
    return os.environ["STRIPE_WEBHOOK_SECRET"]


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, _webhook_secret())
    except StripeSignatureError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_stripe_signature")

    if not sub_service.claim_stripe_event(db, event.id, event.type):
        log.info("stripe_webhook: duplicate event %s ignored", event.id)
        return {"received": True}

    session_obj = event.data.object

    if event.type == "checkout.session.completed":
        sub_service.handle_checkout_completed(db, session_obj)
        # Fire-and-forget referral reward after commit
        _maybe_trigger_referral(background_tasks, db, session_obj)
        # Fire-and-forget annual gift card after commit
        _maybe_trigger_annual_gift_card(background_tasks, db, session_obj)

    elif event.type == "checkout.session.expired":
        # Fired when the Checkout Session expires without payment — session_obj.id = cs_...
        sub_service.handle_checkout_expired(db, session_obj)

    # All other events are acknowledged silently
    return {"received": True}


def _maybe_trigger_annual_gift_card(background_tasks: BackgroundTasks, db: Session, session_obj) -> None:
    """If this is an annual subscription, enqueue trigger_annual_gift_card fire-and-forget."""
    import uuid

    from ratis_core.models.rewards import Subscription

    subscription_id = get_subscription_id_from_metadata(session_obj)
    if not subscription_id:
        return

    # The catch-all keeps the webhook returning 200 to Stripe (a raised
    # exception here would make Stripe retry-storm), but a failed reward must
    # never vanish silently — it is logged with full traceback AND reported to
    # Sentry so a missing annual gift card is observable and can be reconciled.
    try:
        sub = db.query(Subscription).filter(Subscription.id == uuid.UUID(subscription_id)).first()
        if not sub or sub.plan != "annual":
            return
        background_tasks.add_task(trigger_annual_gift_card, sub.user_id, session_obj.id)
    except Exception:
        log.error(
            "_maybe_trigger_annual_gift_card failed for subscription %s — annual gift card NOT issued",
            subscription_id,
            exc_info=True,
        )
        sentry_sdk.capture_exception()


def _maybe_trigger_referral(background_tasks: BackgroundTasks, db: Session, session_obj) -> None:
    """If this user was referred and now has a plan, trigger the reward fire-and-forget."""
    import uuid

    from ratis_core.models.referral import ReferralUse
    from ratis_core.models.rewards import Subscription

    subscription_id = get_subscription_id_from_metadata(session_obj)
    if not subscription_id:
        return

    # See _maybe_trigger_annual_gift_card: the webhook still returns 200 to
    # Stripe, but a failed referral reward is logged with traceback AND sent to
    # Sentry rather than swallowed silently.
    try:
        sub = db.query(Subscription).filter(Subscription.id == uuid.UUID(subscription_id)).first()
        if not sub or not sub.plan:
            return

        referral_use = (
            db.query(ReferralUse)
            .filter(
                ReferralUse.referred_user_id == sub.user_id,
                ReferralUse.plan == sub.plan,
                ReferralUse.rewarded_at.is_(None),
            )
            .first()
        )
        if referral_use:
            from datetime import datetime

            referral_use.rewarded_at = datetime.now(UTC)
            db.commit()  # mark before triggering — idempotent on Stripe retry
            background_tasks.add_task(trigger_referral_reward, sub.user_id, sub.plan)
    except Exception:
        log.error(
            "_maybe_trigger_referral failed for subscription %s — referral reward NOT issued",
            subscription_id,
            exc_info=True,
        )
        sentry_sdk.capture_exception()
