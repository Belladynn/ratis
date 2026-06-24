"""Monetization seed — Wave 4 : subscriptions + gift cards + withdrawals.

Coverage (per ARCH_seed_test_data.md § Step 5) :

- **5 subscriptions** : charlie active monthly (current) + 1 annual past +
  1 cancelled past + 1 expired past + 1 alice trial in-flight
- **8 gift_card_orders for charlie** :
  5 ``source_type='referral_reward'`` (3 ``eligible_at`` past, 2 still
  pending the 30-day anti-churn cooldown — KP-07-bis pattern) +
  3 ``source_type='shop_purchase'`` (cashback redemptions staggered
  across the last 6 months)
- **5 cashback_withdrawals** : 3 charlie (processed last week / pending
  queue / failed RIB) + 2 diane (1 processed pre-DELETE preserved per
  NEVER PURGE + 1 **abandoned** post-DELETE — decision 2026-05-08, see
  ``PROD_CHECKLIST.md § RGPD Cashback handling at account deletion``)
- Side wiring : ``cashback_transactions`` rows of type ``WITHDRAWAL``
  paired 1-1 with the processed withdrawals, and the
  ``users.gift_card_redeemed_ytd_cents`` denorm bumped on Charlie to
  reflect the 3 cashback-funded gift cards.

Schema CHECK mapping (brief → schema)
=====================================
The brief uses descriptive labels that don't all map verbatim to the
existing CHECK constraints. Mapping used :

    ``referral_payout``       → ``source_type='referral_reward'``
    ``cashback_redemption``   → ``source_type='shop_purchase'``
    Subscription "trialing"   → ``status='pending'``, ``payment_ref=NULL``
                                (``payment_ref_coherence`` admits this when
                                ``status NOT IN ('active','expired')``)
    Subscription plan tier    → ``plan IN ('monthly', 'annual')`` only
                                (no separate 'trial' / 'premium_*' tiers)

The ``'abandoned'`` cashback_withdrawals.status is shipped in this PR
via migration ``20260511_2200_cashback_abandoned`` (Pattern A — widen
PG CHECK + mirror in ORM + Pattern-A schema-sync guard re-passes).

Determinism + idempotency
=========================
All UUIDs are derived from a stable byte prefix per domain :

    subscriptions     ``00000000-0000-0000-0002-0000000000XX``
    gift_card_orders  ``00000000-0000-0000-0003-0000000000XX``
    withdrawals       ``00000000-0000-0000-0004-0000000000XX``
    withdrawal txs    ``00000000-0000-0000-0005-0000000000XX``

Idempotency probe : if **any** subscription with one of our deterministic
UUIDs already exists, the whole seed short-circuits. Mirrors Wave 3
``_already_seeded`` style.

Out of scope (tracked, NOT shipped here per R33)
================================================
- ``account_deletion_absorption`` ``cashback_transactions`` row for
  Diane's abandoned withdrawal : requires widening
  ``cashback_transactions.type`` CHECK and a service-side flow in
  ``account_service.delete_account`` — that's a larger surface area
  (CHECK + flow + UX modal — PROD_CHECKLIST.md). Seeding Diane's
  ``abandoned`` row alone faithfully reflects the post-migration DB
  shape ; the absorption transaction lands when the service flow ships.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ratis_core.models.rewards import (
    CashbackTransaction,
    CashbackWithdrawal,
    GiftCardBrand,
    GiftCardOrder,
    Subscription,
)
from ratis_core.models.user import User
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.seed.users import PERSONA_UUIDS


# ============================================================
# Deterministic UUIDs
# ============================================================
def _sub_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0002-{n:012d}")


def _gc_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0003-{n:012d}")


def _wd_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0004-{n:012d}")


def _tx_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0005-{n:012d}")


def _now() -> datetime:
    return datetime.now(UTC)


# ============================================================
# Stripe-ish ID helpers — fake but pattern-matching for screenshot realism
# ============================================================
def _fake_stripe_sub(suffix: str) -> str:
    """Looks like a Stripe subscription id (``sub_XXXXXXXXXXXXXXXX``)."""
    return f"sub_seed{suffix.upper().ljust(16, 'X')}"


def _fake_stripe_pi(suffix: str) -> str:
    """Looks like a Stripe payment intent id."""
    return f"pi_seed{suffix.upper().ljust(20, 'X')}"


def _fake_runa_code(idx: int) -> str:
    """Looks like a Runa gift-card code (``RUNA-XXXX-XXXX``)."""
    block_a = f"{(idx * 0x1F + 0x4A2C) & 0xFFFF:04X}"
    block_b = f"{(idx * 0x37 + 0x91E0) & 0xFFFF:04X}"
    return f"RUNA-{block_a}-{block_b}"


# ============================================================
# Subscriptions
# ============================================================
def _seed_subscriptions(session: Session) -> int:
    """Insert the 5 subscription rows. Returns count inserted."""
    now = _now()
    charlie = PERSONA_UUIDS["charlie"]
    alice = PERSONA_UUIDS["alice"]

    rows = [
        # 1. charlie active monthly — current, renews in 12 days
        Subscription(
            id=_sub_uuid(1),
            user_id=charlie,
            status="active",
            plan="monthly",
            stripe_session_id="cs_seed_charlie_current_monthly",
            price=Decimal("11.99"),
            paid_with="stripe",
            payment_ref=_fake_stripe_sub("CHARLIECURRENT"),
            discount_campaign_code=None,
            discount_amount=None,
            started_at=now - timedelta(days=18),
            expires_at=now + timedelta(days=12),
            cancelled_at=None,
        ),
        # 2. charlie past annual — expired 1y ago
        Subscription(
            id=_sub_uuid(2),
            user_id=charlie,
            status="expired",
            plan="annual",
            stripe_session_id="cs_seed_charlie_past_annual",
            price=Decimal("99.99"),
            paid_with="stripe",
            payment_ref=_fake_stripe_sub("CHARLIEANNUAL"),
            discount_campaign_code=None,
            discount_amount=None,
            started_at=now - timedelta(days=730),
            expires_at=now - timedelta(days=365),
            cancelled_at=None,
        ),
        # 3. charlie cancelled mid-cycle — soft-cancel 3 months ago
        Subscription(
            id=_sub_uuid(3),
            user_id=charlie,
            status="cancelled",
            plan="monthly",
            stripe_session_id="cs_seed_charlie_cancelled",
            price=Decimal("11.99"),
            paid_with="stripe",
            payment_ref=_fake_stripe_sub("CHARLIECANCEL"),
            discount_campaign_code=None,
            discount_amount=None,
            started_at=now - timedelta(days=180),
            # Soft-cancel : ``cancelled_check`` requires cancelled_at IS NOT NULL
            # and ``expires_after_start`` requires expires_at > started_at.
            # cancellation happened mid-cycle, so expires_at = end of paid period.
            expires_at=now - timedelta(days=90),
            cancelled_at=now - timedelta(days=95),
        ),
        # 4. charlie expired monthly — ran out of payment, expired 2 months ago
        Subscription(
            id=_sub_uuid(4),
            user_id=charlie,
            status="expired",
            plan="monthly",
            stripe_session_id="cs_seed_charlie_expired",
            price=Decimal("11.99"),
            paid_with="stripe",
            payment_ref=_fake_stripe_sub("CHARLIEEXPIRED"),
            discount_campaign_code=None,
            discount_amount=None,
            started_at=now - timedelta(days=90),
            expires_at=now - timedelta(days=60),
            cancelled_at=None,
        ),
        # 5. alice trial in-flight — status='pending' (trial → first payment
        # not yet captured ; ``payment_ref_coherence`` admits this since
        # status is neither 'active' nor 'expired'). 14-day trial.
        Subscription(
            id=_sub_uuid(5),
            user_id=alice,
            status="pending",
            plan="monthly",
            stripe_session_id="cs_seed_alice_trial",
            price=Decimal("11.99"),
            paid_with="stripe",
            payment_ref=None,  # trial — no payment captured yet
            discount_campaign_code=None,
            discount_amount=None,
            started_at=now - timedelta(minutes=2),
            expires_at=now + timedelta(days=13, hours=23, minutes=58),
            cancelled_at=None,
        ),
    ]
    inserted = 0
    for row in rows:
        session.add(row)
        inserted += 1
    session.flush()
    return inserted


# ============================================================
# Gift card orders
# ============================================================
def _resolve_brand_ids(session: Session) -> dict[str, uuid.UUID]:
    """Return a ``name -> brand_id`` mapping for the boutique catalogue."""
    rows = session.execute(select(GiftCardBrand)).scalars().all()
    return {b.name: b.id for b in rows}


def _seed_gift_cards(session: Session) -> int:
    """Insert 8 gift card orders for charlie. Returns count inserted."""
    now = _now()
    charlie = PERSONA_UUIDS["charlie"]
    brands = _resolve_brand_ids(session)
    # Fall back to the first available brand if a name moved — keeps the
    # seed running even if the boutique catalogue gets renamed.
    if not brands:
        raise RuntimeError("gift_card_brands is empty — boutique migration not run ?")
    catalog: list[uuid.UUID] = list(brands.values())

    # Stable brand picker (deterministic — same index → same brand across runs).
    def _brand_at(idx: int) -> uuid.UUID:
        canonical_order = ["Amazon.fr", "Carrefour", "Decathlon", "Sephora", "Spotify"]
        for name in canonical_order:
            if name in brands and idx == canonical_order.index(name):
                return brands[name]
        # Final fallback : modulo over whatever is in the DB.
        return catalog[idx % len(catalog)]

    rows: list[GiftCardOrder] = []

    # ----- 5 referral_reward gift cards -----
    # 3 eligible (eligible_at past → ``issued``)
    for i in range(3):
        rows.append(
            GiftCardOrder(
                id=_gc_uuid(i + 1),
                user_id=charlie,
                brand_id=_brand_at(i),
                denomination=500 + i * 500,  # 5€ / 10€ / 15€
                status="issued",
                source_type="referral_reward",
                source_ref_id=f"seed-referral-eligible-{i + 1:02d}",
                provider_order_id=f"runa-ord-ref-{i + 1:02d}",
                code=_fake_runa_code(100 + i),
                eligible_at=now - timedelta(days=60 + i * 20),  # past — eligible
                issued_at=now - timedelta(days=60 + i * 20),
                failed_at=None,
                created_at=now - timedelta(days=90 + i * 20),
            )
        )

    # 2 still in 30-day anti-churn cooldown (eligible_at future → ``pending``)
    # Mirrors KP-07-bis : referral_reward gift_cards are NOT issued until
    # 30 days post-referral conversion. Eligible_at = creation + 30d.
    for i in range(2):
        created = now - timedelta(days=10 + i * 5)
        rows.append(
            GiftCardOrder(
                id=_gc_uuid(i + 4),
                user_id=charlie,
                brand_id=_brand_at((i + 3) % 5),
                denomination=1000 + i * 500,  # 10€ / 15€
                status="pending",
                source_type="referral_reward",
                source_ref_id=f"seed-referral-cooldown-{i + 1:02d}",
                # No provider_order_id / code yet — anti-churn cooldown.
                provider_order_id=None,
                code=None,
                eligible_at=created + timedelta(days=30),  # future
                issued_at=None,
                failed_at=None,
                created_at=created,
            )
        )

    # ----- 3 cashback_redemption (shop_purchase) -----
    # Staggered across 6 months — each represents a redemption of cashback
    # for a gift card. eligible_at = NULL (no anti-churn for boutique).
    cashback_offsets_days = [180, 90, 30]
    cashback_denoms_cents = [2000, 1500, 5000]  # 20€ / 15€ / 50€
    for i, (offset, denom) in enumerate(zip(cashback_offsets_days, cashback_denoms_cents, strict=False)):
        rows.append(
            GiftCardOrder(
                id=_gc_uuid(i + 6),
                user_id=charlie,
                brand_id=_brand_at(i % 5),
                denomination=denom,
                status="issued",
                source_type="shop_purchase",
                source_ref_id=f"seed-cashback-redeem-{i + 1:02d}",
                provider_order_id=f"runa-ord-cb-{i + 1:02d}",
                code=_fake_runa_code(200 + i),
                eligible_at=None,
                issued_at=now - timedelta(days=offset),
                failed_at=None,
                created_at=now - timedelta(days=offset),
            )
        )

    for row in rows:
        session.add(row)
    session.flush()

    # Update charlie's denorm running total. The 3 cashback_redemptions
    # totalled 85€ this year — bump the YTD field accordingly. Set to the
    # total (not increment) for idempotency : a re-run after a balance edit
    # would otherwise compound.
    cashback_redeem_total = sum(cashback_denoms_cents)  # 8500c = 85€
    charlie_user = session.get(User, charlie)
    if charlie_user is not None:
        charlie_user.gift_card_redeemed_ytd_cents = cashback_redeem_total
    session.flush()

    return len(rows)


# ============================================================
# Cashback withdrawals + paired WITHDRAWAL transactions
# ============================================================
def _seed_withdrawals(session: Session) -> int:
    """Insert 5 withdrawals (3 charlie + 2 diane) + paired tx rows.

    Returns count of withdrawal rows inserted (the tx count is the count
    of *processed* + *abandoned* withdrawals, i.e. those that carry an
    accounting fact).
    """
    now = _now()
    charlie = PERSONA_UUIDS["charlie"]
    diane = PERSONA_UUIDS["diane"]
    inserted_withdrawals = 0

    # ----- Charlie #1 : processed last week (50€) -----
    # Triggers WITHDRAWAL tx row + processed_at = -2d.
    c1_tx_id = _tx_uuid(1)
    session.add(
        CashbackTransaction(
            id=c1_tx_id,
            user_id=charlie,
            type="WITHDRAWAL",
            amount=5000,
            status="confirmed",
            product_ean=None,
            affiliate_offer_id=None,
            boost_applied=False,
            distributed_at=now - timedelta(days=7),
            scan_id=None,
            parent_transaction_id=None,
            parent_type=None,
            created_at=now - timedelta(days=7),
        )
    )
    session.add(
        CashbackWithdrawal(
            id=_wd_uuid(1),
            user_id=charlie,
            amount=5000,
            status="processed",
            cashback_transaction_id=c1_tx_id,
            # provider_coherence : ref + initiated_at set together.
            payment_provider_ref="runa_seed_payout_c01",
            provider_initiated_at=now - timedelta(days=7),
            last_reconciled_at=now - timedelta(days=2),
            requested_at=now - timedelta(days=7),
            processed_at=now - timedelta(days=2),
            failure_reason=None,
        )
    )
    inserted_withdrawals += 1

    # ----- Charlie #2 : pending queue admin (40€) -----
    # No cashback_transaction yet (the WITHDRAWAL tx + balance debit
    # happens at REQUEST time per R10 in real prod ; but the seed simply
    # mirrors the ON-DISK shape — pending row + linked tx). Use a tx row
    # with status='pending'.
    c2_tx_id = _tx_uuid(2)
    session.add(
        CashbackTransaction(
            id=c2_tx_id,
            user_id=charlie,
            type="WITHDRAWAL",
            amount=4000,
            status="pending",
            product_ean=None,
            affiliate_offer_id=None,
            boost_applied=False,
            distributed_at=None,
            scan_id=None,
            parent_transaction_id=None,
            parent_type=None,
            created_at=now - timedelta(days=1),
        )
    )
    session.add(
        CashbackWithdrawal(
            id=_wd_uuid(2),
            user_id=charlie,
            amount=4000,
            status="pending",
            cashback_transaction_id=c2_tx_id,
            payment_provider_ref=None,
            provider_initiated_at=None,
            last_reconciled_at=None,
            requested_at=now - timedelta(days=1),
            processed_at=None,
            failure_reason=None,
        )
    )
    inserted_withdrawals += 1

    # ----- Charlie #3 : failed RIB rejected (30€) -----
    # status='failed' with failure_reason populated (failure_check CHECK).
    # Per R10 the WITHDRAWAL tx is created at request-time and then the
    # balance is refunded via a parent_type='withdrawal_refund' child tx
    # in prod ; here we model the failed state at point-in-time : the
    # WITHDRAWAL tx exists, the withdrawal row carries the failure reason.
    c3_tx_id = _tx_uuid(3)
    session.add(
        CashbackTransaction(
            id=c3_tx_id,
            user_id=charlie,
            type="WITHDRAWAL",
            amount=3000,
            status="refused",
            product_ean=None,
            affiliate_offer_id=None,
            boost_applied=False,
            distributed_at=None,
            scan_id=None,
            parent_transaction_id=None,
            parent_type=None,
            created_at=now - timedelta(days=3),
        )
    )
    session.add(
        CashbackWithdrawal(
            id=_wd_uuid(3),
            user_id=charlie,
            amount=3000,
            status="failed",
            cashback_transaction_id=c3_tx_id,
            payment_provider_ref=None,
            provider_initiated_at=None,
            last_reconciled_at=now - timedelta(days=2),
            requested_at=now - timedelta(days=3),
            processed_at=None,
            failure_reason="rib_rejected_iban_invalid",
        )
    )
    inserted_withdrawals += 1

    # ----- Diane #1 : processed pre-DELETE (25€) — preserved NEVER PURGE -----
    # This row was created and paid out BEFORE diane's account deletion ;
    # the user_id link survives (ON DELETE SET NULL is in play only for
    # the *user-row* removal — diane is anonymised in-place, the row
    # still points to her id). Legal retention : NEVER PURGE.
    d1_tx_id = _tx_uuid(4)
    session.add(
        CashbackTransaction(
            id=d1_tx_id,
            user_id=diane,
            type="WITHDRAWAL",
            amount=2500,
            status="confirmed",
            product_ean=None,
            affiliate_offer_id=None,
            boost_applied=False,
            distributed_at=now - timedelta(days=90),  # -3mo
            scan_id=None,
            parent_transaction_id=None,
            parent_type=None,
            created_at=now - timedelta(days=92),
        )
    )
    session.add(
        CashbackWithdrawal(
            id=_wd_uuid(4),
            user_id=diane,
            amount=2500,
            status="processed",
            cashback_transaction_id=d1_tx_id,
            payment_provider_ref="runa_seed_payout_d01",
            provider_initiated_at=now - timedelta(days=92),
            last_reconciled_at=now - timedelta(days=88),
            requested_at=now - timedelta(days=92),
            processed_at=now - timedelta(days=87),  # ~2.9mo ago
            failure_reason=None,
        )
    )
    inserted_withdrawals += 1

    # ----- Diane #2 : abandoned post-DELETE (15€) — decision 2026-05-08 -----
    # Created BEFORE deletion (status=pending then). At DELETE time the
    # withdrawal transitioned to 'abandoned' per the new ``status_check``
    # widening (migration ``20260511_2200_cashback_abandoned``).
    # NOTE : the absorbing ``account_deletion_absorption`` cashback_transaction
    # row is NOT seeded here — that requires a separate CHECK widening on
    # ``cashback_transactions.type`` + a service-side flow (PROD_CHECKLIST.md
    # § RGPD Cashback handling). When that ships we extend this seed.
    session.add(
        CashbackWithdrawal(
            id=_wd_uuid(5),
            user_id=diane,
            amount=1500,
            status="abandoned",
            # No cashback_transaction_id : the absorption tx hasn't shipped
            # yet. ``transaction_required`` CHECK only fires for status='processed'.
            cashback_transaction_id=None,
            payment_provider_ref=None,
            provider_initiated_at=None,
            last_reconciled_at=None,
            requested_at=now - timedelta(days=70),
            processed_at=None,
            failure_reason=None,
        )
    )
    inserted_withdrawals += 1

    session.flush()
    return inserted_withdrawals


# ============================================================
# Idempotency probe
# ============================================================
def _already_seeded(session: Session) -> bool:
    """If our deterministic charlie subscription #1 exists, skip the whole pass."""
    existing = session.execute(select(Subscription.id).where(Subscription.id == _sub_uuid(1)).limit(1)).first()
    return existing is not None


# ============================================================
# Public entrypoint
# ============================================================
def seed_monetization(session: Session) -> None:
    """Insert subscriptions + gift cards + withdrawals. See ARCH § Step 5.

    Idempotent — re-runs short-circuit if Charlie's active monthly sub is
    already present.
    """
    if _already_seeded(session):
        print("[monetization] already seeded — skipping (idempotent)")
        return

    print("[monetization] seeding charlie/alice/diane monetization rows…")
    n_subs = _seed_subscriptions(session)
    n_gifts = _seed_gift_cards(session)
    n_wd = _seed_withdrawals(session)
    session.flush()
    print(f"[monetization] done — {n_subs} subscriptions, {n_gifts} gift_card_orders, {n_wd} cashback_withdrawals")
