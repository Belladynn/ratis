"""
Referral service — business logic for the rewards side of referrals.

Covers the full subscription-referral flow :
  1. Look up the X→Y link (no-op if none, idempotent if already rewarded)
  2. Award CAB + XP to X (the referrer)
  3. Increment the referral community-challenge for X
  4. Enqueue a delayed gift card order for X (eligible_at = NOW() + 30d)

Previously this lived inline in ``cab_service.handle_referral_reward`` +
``handle_referral_xp`` + the route. All three awarded to ``referred_user_id``
(Y) instead of the referrer (X) — this service corrects that semantic.

See ``ARCH_referral.md`` § décisions actées.
"""

from __future__ import annotations

import logging
import uuid

from repositories.cab_repository import award_cab
from repositories.challenge_repository import maybe_increment_challenge
from repositories.gift_card_repository import insert_gift_card_order
from repositories.referral_repository import (
    create_code_for_user,
    create_use,
    get_by_code,
    get_code_for_user,
    get_for_referred_user,
    get_history_for_user,
    has_referral_use,
    mark_rewarded,
)
from repositories.xp_repository import award_xp
from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


def handle_subscription_referral(
    db: Session,
    referred_user_id: uuid.UUID,
    plan: str,
    cfg: dict,
) -> uuid.UUID | None:
    """
    Process a referred user's subscription event : credit the referrer with
    CAB + XP + challenge progress + a delayed gift card.

    Silent no-op (returns None) in two cases :
      - No referral link exists for ``referred_user_id`` (Stripe webhook
        fired for a regular subscriber without a referrer)
      - The link is already marked rewarded (idempotency — second call for
        the same filleul)

    The caller (route handler) commits the transaction.

    Returns the referrer's user_id if the rewards were applied this call,
    None otherwise.
    """
    rewards_cfg = cfg.get("rewards", {})
    referral_cfg = cfg.get("referral", {})

    key = f"cab_referral_{plan}"
    cab_amount = rewards_cfg.get(key)
    if cab_amount is None:
        raise ValueError(f"No CAB amount configured for referral plan={plan!r}")

    link = get_for_referred_user(db, referred_user_id)
    if link is None:
        return None

    if not mark_rewarded(db, link.referral_use_id, plan):
        # Already rewarded — idempotent no-op
        return None

    # 1. CAB — flat, no multipliers (bonus exceptionnel — cf ARCH_cab_economy)
    if cab_amount > 0:
        award_cab(
            db,
            link.referrer_user_id,
            cab_amount,
            "referral",
            reference_id=link.referral_use_id,
            reference_type="referral",
            apply_streak_multiplier=False,
        )

    # 2. XP — same "bonus exceptionnel" posture : no multipliers
    xp_cfg = cfg.get("xp", {})
    xp_amount = xp_cfg.get("xp_per_referral", 0)
    if xp_amount > 0:
        award_xp(
            db,
            link.referrer_user_id,
            xp_amount,
            "referral",
            reference_id=link.referral_use_id,
            reference_type="referral",
            level_base=xp_cfg.get("level_base", 100),
            apply_streak_multiplier=False,
        )

    # 3. Community challenge — progress counts for the referrer (X did the
    #    acquisition), not the referred (Y just subscribed).
    maybe_increment_challenge(db, link.referrer_user_id, "referral")

    # 4. Gift card — enqueue with delayed eligibility (anti-churn 30d).
    #    Skipped if no brand configured (bootstrap state pre-Runa KYB).
    _enqueue_delayed_gift_card(
        db,
        referrer_user_id=link.referrer_user_id,
        referral_use_id=link.referral_use_id,
        referral_cfg=referral_cfg,
    )

    # Achievements V1 — fire-and-forget. The `referral_paid` event is
    # emitted for the REFERRER (X), whose `gift_card_orders.eligible_at`
    # was just set in `_enqueue_delayed_gift_card`. The handler counts
    # rows with `source_type='referral_reward' AND eligible_at IS NOT NULL`.
    try:
        from services import achievement_service

        achievement_service.check_achievements(
            db,
            user_id=link.referrer_user_id,
            event_type="referral_paid",
            payload={
                "referral_use_id": str(link.referral_use_id),
                "plan": plan,
            },
        )
    except Exception:
        _log.exception(
            "achievement_hook_referral_paid_failed",
            extra={"user_id": str(link.referrer_user_id)},
        )

    return link.referrer_user_id


def _enqueue_delayed_gift_card(
    db: Session,
    *,
    referrer_user_id: uuid.UUID,
    referral_use_id: uuid.UUID,
    referral_cfg: dict,
) -> None:
    """
    Insert a pending gift_card_orders row with eligible_at = NOW() + delay.

    The row is picked up by the ``ratis_batch_referral_payout`` daily cron,
    which verifies the referred user is still subscribed before calling
    ``issue_gift_card`` against Runa.

    No-op (with warning) if no gift_card_brand_id is configured yet —
    expected during bootstrap before the Runa KYB completes.
    """
    brand_id = referral_cfg.get("gift_card_brand_id")
    if not brand_id:
        _log.warning(
            "referral.gift_card_brand_id not set — skipping gift card enqueue for referral_use_id=%s",
            referral_use_id,
        )
        return

    amount = referral_cfg.get("gift_card_amount_cents", 500)
    delay_days = referral_cfg.get("eligibility_delay_days", 30)

    # Use insert_gift_card_order (idempotent via UNIQUE source_type+
    # source_ref_id), then patch eligible_at in a follow-up UPDATE (the
    # repo primitive doesn't set it). Trade-off : 2 SQL statements instead
    # of 1, but we reuse the existing repo primitive without changing it.
    order_id = insert_gift_card_order(
        db,
        user_id=referrer_user_id,
        brand_id=uuid.UUID(brand_id) if isinstance(brand_id, str) else brand_id,
        denomination_cents=amount,
        source_type="referral_reward",
        source_ref_id=str(referral_use_id),
    )
    db.execute(
        text(
            "UPDATE gift_card_orders "
            "SET eligible_at = now() + (:delay_days * INTERVAL '1 day') "
            "WHERE id = :oid AND eligible_at IS NULL"
        ),
        {"delay_days": delay_days, "oid": order_id},
    )


# ============================================================
# USER-FACING API — called from GET /rewards/referral/{code,history}
# ============================================================


def get_or_create_code(db: Session, user_id: uuid.UUID) -> dict:
    """
    Return the user's referral code, creating it lazily on first access.

    Shape : ``{"code": "A1B2C3D4", "created_at": datetime}``. Caller commits.
    """
    existing = get_code_for_user(db, user_id)
    if existing is not None:
        return {"code": existing.code, "created_at": existing.created_at}

    created = create_code_for_user(db, user_id)
    return {"code": created.code, "created_at": created.created_at}


def get_history(db: Session, user_id: uuid.UUID, rewards_cfg: dict) -> dict:
    """
    Assemble the full GET /rewards/referral/history response.

    Shape :
      {
        "code": "A1B2C3D4",
        "stats": {total_uses, rewarded_uses, total_cab_earned},
        "uses": [{referred_user_display_name, plan, rewarded_at, created_at,
                  status}],
      }

    ``total_cab_earned`` is computed from the rewards_cfg (plan → CAB amount),
    not from the actual cabecoin_transactions — the DB source of truth is the
    config at the moment each reward was issued, but for display we use the
    *current* config so the user sees today's rate (slight bias, negligible
    for stats).

    ``status`` is derived : 'rewarded' if rewarded_at is set, else 'pending'.
    """
    # Make sure the code exists first (lazy-create if needed) — the user might
    # call history before /code, we shouldn't 404 them.
    code_obj = get_code_for_user(db, user_id)
    if code_obj is None:
        code_obj = create_code_for_user(db, user_id)

    uses = get_history_for_user(db, user_id)
    cab_monthly = rewards_cfg.get("cab_referral_monthly", 0)
    cab_annual = rewards_cfg.get("cab_referral_annual", 0)

    total = len(uses)
    rewarded = sum(1 for u in uses if u["rewarded_at"] is not None)
    total_cab = sum(
        (cab_annual if u["plan"] == "annual" else cab_monthly) for u in uses if u["rewarded_at"] is not None
    )

    # Enrich each use with a derived status for the UI
    for u in uses:
        u["status"] = "rewarded" if u["rewarded_at"] is not None else "pending"

    return {
        "code": code_obj.code,
        "stats": {
            "total_uses": total,
            "rewarded_uses": rewarded,
            "total_cab_earned": total_cab,
        },
        "uses": uses,
    }


# ============================================================
# SIGNUP BONUS — called from POST /rewards/referral/signup-bonus (INTERNAL_KEY)
# ============================================================


def award_referral_signup_bonus(
    db: Session,
    referred_user_id: uuid.UUID,
    cfg: dict,
) -> bool:
    """
    Credit Y with their signup bonus (150 CAB flat) if they have a referral
    link. Called by ratis_auth after ``register()`` creates the referral_uses
    row — fire-and-forget, never raises.

    Idempotent : a second call for the same user is a no-op (detects the
    existing ``cabecoin_transactions`` row).

    Returns True if a CAB award happened this call, False otherwise (no link,
    already awarded, or zero-amount config).
    """
    row = db.execute(
        text("SELECT id FROM referral_uses WHERE referred_user_id = :ruid"),
        {"ruid": referred_user_id},
    ).first()
    if row is None:
        # Ratis_auth called the hook for a user who didn't actually provide a
        # valid code. Treat as silent no-op.
        return False

    use_id = row.id

    already = db.execute(
        text(
            "SELECT 1 FROM cabecoin_transactions "
            "WHERE user_id = :uid AND reference_id = :ref "
            "  AND reference_type = 'referral' AND direction = 'credit' "
            "LIMIT 1"
        ),
        {"uid": referred_user_id, "ref": use_id},
    ).scalar()
    if already is not None:
        # Bonus already awarded (retry from ratis_auth or admin datafix race)
        return False

    amount = cfg.get("rewards", {}).get("cab_referral_signup_bonus", 0)
    if amount <= 0:
        return False

    award_cab(
        db,
        referred_user_id,
        amount,
        "referral",
        reference_id=use_id,
        reference_type="referral",
        apply_streak_multiplier=False,
    )
    return True


# ============================================================
# ADMIN API — called from POST /admin/referral/link (ADMIN_API_KEY)
# ============================================================


def link_manually_and_reward(
    db: Session,
    *,
    referred_user_id: uuid.UUID,
    code: str,
    admin_operator_id: str,
    cfg: dict,
) -> dict:
    """
    Support-driven datafix flow : user Y forgot to enter code X at signup,
    contacts support, support creates the link manually.

    Effects :
      1. Create ``referral_uses`` (X→Y) — errors 400 on self-parrainage,
         404 on invalid code, 409 if Y already linked, 404 on missing user
      2. Award signup bonus Y (150 CAB) flat — same as the signup hook would
      3. If Y is already subscribed (``users.subscription_status = 'active'``),
         trigger the referrer reward immediately (CAB + XP + challenge +
         delayed gift card)
      4. Log the admin operation (stdout ; a dedicated audit_log table is a
         V2 upgrade — for V1 we rely on process-level log retention)

    Returns a dict describing what happened — the route serialises it to JSON.

    Raises :
      - ValueError("invalid_code") — code not found
      - ValueError("self_parrainage") — Y tried to use their own code
      - ValueError("user_not_found") — referred_user_id doesn't exist
      - ValueError("already_linked") — Y already has a referral_uses row

    The caller (route handler) commits the transaction.
    """
    from ratis_core.models.user import User

    referral = get_by_code(db, code)
    if referral is None:
        raise ValueError("invalid_code")
    if referral.user_id is None:
        # Orphaned code (referrer account deleted) — nothing to credit
        raise ValueError("invalid_code")
    if referral.user_id == referred_user_id:
        raise ValueError("self_parrainage")

    referred = db.get(User, referred_user_id)
    if referred is None:
        raise ValueError("user_not_found")

    if has_referral_use(db, referred_user_id):
        raise ValueError("already_linked")

    use = create_use(db, referral.id, referred_user_id)

    # Signup bonus to Y — flat
    rewards_cfg = cfg.get("rewards", {})
    signup_bonus = rewards_cfg.get("cab_referral_signup_bonus", 0)
    if signup_bonus > 0:
        award_cab(
            db,
            referred_user_id,
            signup_bonus,
            "referral",
            reference_id=use.id,
            reference_type="referral",
            apply_streak_multiplier=False,
        )

    result: dict = {
        "detail": "link_created",
        "referral_use_id": use.id,
        "signup_bonus_awarded": signup_bonus,
        "subscription_reward_triggered": False,
        "admin_operator_id": admin_operator_id,
    }

    # If Y is already subscribed, trigger the X reward immediately so the
    # support call resolves fully in one hit. Otherwise rely on the future
    # Stripe webhook.
    plan = _get_active_plan(db, referred_user_id)
    if plan is not None:
        referrer_id = handle_subscription_referral(db, referred_user_id, plan, cfg)
        if referrer_id is not None:
            result["detail"] = "link_created_and_rewarded"
            result["subscription_reward_triggered"] = True
            result["cab_awarded_to_referrer"] = rewards_cfg.get(f"cab_referral_{plan}", 0)

    _log.info(
        "referral.admin_link admin=%s referred=%s code=%s status=%s",
        admin_operator_id,
        referred_user_id,
        code,
        result["detail"],
    )
    return result


def _get_active_plan(db: Session, user_id: uuid.UUID) -> str | None:
    """
    Return 'monthly' / 'annual' if the user has an active subscription,
    None otherwise.

    Queries the ``subscriptions`` table for the most recent active row.
    Returns None if no active subscription — the webhook hasn't marked a
    subscription 'active' yet, or the user is free tier.
    """
    row = db.execute(
        text(
            "SELECT plan FROM subscriptions WHERE user_id = :uid AND status = 'active' ORDER BY started_at DESC LIMIT 1"
        ),
        {"uid": user_id},
    ).first()
    if row is None or row.plan not in ("monthly", "annual"):
        return None
    return row.plan
