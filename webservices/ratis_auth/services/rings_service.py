"""Atomic ring-claim for the dashboard ROI feature.

Each POST /account/rings/claim increments ``user_savings_snapshot.rings_consumed``
by exactly 1 when the user still has a pending ring. Concurrent POSTs race
cleanly :

- Compute live eligibility (snapshot lifetime + delta since last batch).
- Run UPDATE … SET rings_consumed = rings_consumed + 1
       WHERE user_id = :uid AND rings_consumed < :eligible RETURNING rings_consumed.
- If no row returned → ``nothing_to_claim``. Otherwise → ``claimed``.

Because the UPDATE holds a row-level lock between SELECT and UPDATE, two
concurrent requests with eligibility=2 and rings_consumed=0 will produce
rings_consumed=1 and 2 respectively, never both =1. Eligibility itself is a
derived number — it can grow between claims if a fresh scan is accepted, but
the UPDATE guard ensures rings_consumed never exceeds the eligibility at the
moment the row was updated.
"""

from __future__ import annotations

from ratis_core.models.user import User
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.stats_service import compute_account_stats

CLAIMED = "claimed"
NOTHING_TO_CLAIM = "nothing_to_claim"


def claim_ring(db: Session, user: User) -> dict:
    """Atomic attempt to break one pending ROI ring.

    Returns a dict shaped as :
        {
            "animation": "claimed" | "nothing_to_claim",
            "rings_consumed": int,
            "pending_rings": int,
            "subscription_price_cents": int,
        }
    """
    # `compute_account_stats` also materialises the snapshot row on first access.
    stats = compute_account_stats(db, user)
    rings = stats["rings"]

    if rings["pending_rings"] <= 0:
        # Short-circuit : no UPDATE, no write, no lock contention. This also
        # guarantees the "no uncommitted writes" invariant when the user has
        # nothing to claim — keeps the hot path truly read-only.
        return {
            "animation": NOTHING_TO_CLAIM,
            "rings_consumed": rings["rings_consumed"],
            "pending_rings": rings["pending_rings"],
            "subscription_price_cents": rings["subscription_price_cents"],
        }

    eligible = rings["rings_consumed"] + rings["pending_rings"]
    row = (
        db.execute(
            text(
                "UPDATE user_savings_snapshot "
                "SET rings_consumed = rings_consumed + 1 "
                "WHERE user_id = :uid AND rings_consumed < :eligible "
                "RETURNING rings_consumed"
            ),
            {"uid": str(user.id), "eligible": eligible},
        )
        .mappings()
        .one_or_none()
    )

    if row is None:
        # Race : another concurrent claim beat us. Pending may now be 0.
        db.commit()
        return {
            "animation": NOTHING_TO_CLAIM,
            "rings_consumed": rings["rings_consumed"],
            "pending_rings": rings["pending_rings"],
            "subscription_price_cents": rings["subscription_price_cents"],
        }

    new_consumed = int(row["rings_consumed"])
    db.commit()

    new_pending = max(0, eligible - new_consumed)
    return {
        "animation": CLAIMED,
        "rings_consumed": new_consumed,
        "pending_rings": new_pending,
        "subscription_price_cents": rings["subscription_price_cents"],
    }
