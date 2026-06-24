# webservices/ratis_rewards/services/gift_card_cap_service.py
"""Central DAS2 fiscal-cap reservation for gift-card issuance (audit H4).

Every gift-card issuance — boutique, annual_subscription, battlepass_milestone,
referral_reward — funnels through ``gift_card_service.issue_gift_card`` which
calls ``reserve_gift_card_cap`` BEFORE the Runa HTTP call. The reservation
increments the user's denormalised YTD counter under a per-user advisory lock ;
``release_gift_card_cap`` decrements it on failure. The 1199 € cap value is
``boutique.cap_annual_cents`` in ratis_settings.json (canonical fiscal cap —
the key lives under ``boutique.*`` for historical reasons).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CapDecision:
    """Outcome of a cap reservation.

    outcome ``"allow"``  — reserved ; the caller proceeds with issuance.
    outcome ``"defer"``  — over cap, earned reward ; the caller sets the
                           order's ``eligible_at`` to ``deferred_until`` and
                           leaves it ``pending``.
    outcome ``"block"``  — over cap, boutique ; the caller fails the order.
    """

    outcome: str
    deferred_until: datetime | None = None


def next_jan_1_utc() -> datetime:
    """First of January FOLLOWING the current date, 00:00 UTC."""
    now = datetime.now(UTC)
    return datetime(now.year + 1, 1, 1, tzinfo=UTC)


def _cap_annual_cents() -> int:
    return int(load_settings()["boutique"]["cap_annual_cents"])


def reserve_gift_card_cap(db: Session, order_id: uuid.UUID, *, allow_defer: bool) -> CapDecision:
    """Reserve an order's denomination against the user's annual fiscal cap.

    Atomic within the caller's transaction — does NOT commit. Idempotent : a
    re-call on an already-reserved order returns ``allow`` without
    double-counting.
    """
    row = db.execute(
        text("SELECT user_id, denomination, cap_reserved_cents FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).first()
    if row is None:
        return CapDecision(outcome="block")
    if row.cap_reserved_cents and int(row.cap_reserved_cents) > 0:
        return CapDecision(outcome="allow")  # already reserved — cheap pre-lock fast-path

    user_id = row.user_id
    amount = int(row.denomination)

    # Per-user advisory lock — serialises concurrent issuances for one user
    # across processes/connections. Transaction-scoped, auto-released at
    # commit/rollback.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"gift_card_cap:{user_id}"},
    )
    # Re-check the reservation UNDER the lock : a concurrent caller may have
    # reserved this same order between the pre-lock fast-path above and the
    # lock acquisition. Without this, that caller's idempotent retry would
    # read the already-incremented ytd and wrongly return block/defer.
    recheck = db.execute(
        text("SELECT cap_reserved_cents FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).first()
    if recheck and recheck.cap_reserved_cents and int(recheck.cap_reserved_cents) > 0:
        return CapDecision(outcome="allow")

    ytd_row = db.execute(
        text("SELECT gift_card_redeemed_ytd_cents AS v FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).first()
    ytd = int(ytd_row.v) if ytd_row else 0

    if ytd + amount <= _cap_annual_cents():
        db.execute(
            text("UPDATE users SET gift_card_redeemed_ytd_cents = gift_card_redeemed_ytd_cents + :amt WHERE id = :uid"),
            {"amt": amount, "uid": user_id},
        )
        db.execute(
            text("UPDATE gift_card_orders SET cap_reserved_cents = :amt WHERE id = :oid"),
            {"amt": amount, "oid": order_id},
        )
        return CapDecision(outcome="allow")

    if allow_defer:
        return CapDecision(outcome="defer", deferred_until=next_jan_1_utc())
    return CapDecision(outcome="block")


def release_gift_card_cap(db: Session, order_id: uuid.UUID) -> None:
    """Release an order's cap reservation (decrement the user's YTD). Idempotent.

    Atomic within the caller's transaction — does NOT commit. A no-op when the
    order was never reserved (``cap_reserved_cents == 0``).
    """
    row = db.execute(
        text("SELECT user_id, cap_reserved_cents FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).first()
    if row is None or not row.cap_reserved_cents:
        return
    amount = int(row.cap_reserved_cents)
    db.execute(
        text(
            "UPDATE users SET gift_card_redeemed_ytd_cents = "
            "GREATEST(0, gift_card_redeemed_ytd_cents - :amt) WHERE id = :uid"
        ),
        {"amt": amount, "uid": row.user_id},
    )
    db.execute(
        text("UPDATE gift_card_orders SET cap_reserved_cents = 0 WHERE id = :oid"),
        {"oid": order_id},
    )
