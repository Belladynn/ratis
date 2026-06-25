"""
XP repository — raw SQL for atomicity.

All write operations work within the caller's session transaction.
The caller is responsible for commit() after all operations succeed.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.streak_repository import get_streak_multiplier as _get_streak_multiplier

VALID_XP_REASONS = frozenset(
    {
        "receipt_scan",
        "label_scan",
        # Legacy reason kept accepted for historical rows minted before
        # phase B (PR #325) — runtime now emits "product_identification".
        "barcode_scan",
        "product_identification",  # Phase B — rename of barcode_scan.
        "fill_product_field",  # Phase B — manual product attribute filling.
        "scan_distinct",  # Phase B — diversity badge / mission progress.
        "promo_found",  # Phase B — user-flagged in-store promo.
        "price_compared",
        "mission_completed",
        "battlepass_milestone",
        "referral",
        "feed_jack",
        # Legacy reason kept accepted for historical XP rows minted before
        # the Buffer + Burst refonte (2026-05-09). The runtime now emits
        # 'mission_burst' for Burst palier claims and 'mission_completed'
        # for non-bursted mission claims.
        "stonks_completion",
        "challenge_milestone",  # Défi communautaire — keep in sync with _XP_REASONS
        "mission_burst",  # Buffer + Burst — Burst palier claim XP credit.
    }
)


def _compute_level(balance: int | Any, level_base: int) -> int:
    """Return the level corresponding to the given cumulative XP balance.

    Threshold to reach level n: level_base * (2^n - 1).
    Uses pure integer arithmetic — supports balances beyond 2^200.

    Closed-form equivalent (mirror of the SQL expression used in
    ``award_xp`` for atomic level recomputation) :

        level = floor(log2(balance / level_base + 1))

    The Python implementation stays iterative + integer-only because
    ``math.log2`` would lose precision past ~2^53. The SQL side uses
    ``log(2.0, …)`` on ``numeric`` which keeps full precision for the
    XP magnitudes we'll ever store.
    """
    balance = int(balance)
    if balance <= 0 or level_base <= 0:
        return 0
    level = 0
    threshold = level_base  # level_base * (2^1 - 1) = level_base
    while balance >= threshold:
        level += 1
        threshold += level_base << level  # next delta: level_base * 2^level
    return level


def get_xp_balance(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """Return the user's XP balance and level (zeros if no row)."""
    row = db.execute(
        text("SELECT balance, level FROM user_xp_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    if not row:
        return {"balance": 0, "level": 0}
    return {"balance": int(row.balance), "level": row.level}


def award_xp(
    db: Session,
    user_id: uuid.UUID,
    amount: int | Any,
    reason: str,
    reference_id: uuid.UUID | None = None,
    reference_type: str | None = None,
    level_base: int = 100,
    apply_streak_multiplier: bool = True,
    community_multiplier: float | None = None,
) -> dict[str, Any]:
    """
    Credit XP to a user — atomic within the caller's transaction.

    Steps:
    1. Apply streak multiplier if active (min(streak_days * 5%, 100%))
    2. INSERT xp_transactions (stores effective_amount)
    3. UPSERT user_xp_balance — ``level`` is recomputed inline in the
       SQL UPSERT from the POST-UPDATE balance (audit F-RW-5). The
       level used to be computed in Python from a stale pre-UPDATE
       read, leaving ``level`` out of sync on concurrent grants : two
       `+100` awards on a user at balance=100 would both read
       balance=100 / level=1, both compute new=200 / level=1, and the
       second UPDATE would atomically increment balance to 300 but
       overwrite ``level`` back to its stale value of 1 (true level
       for 300 is 2). The closed-form
       ``floor(log2(balance / level_base + 1))`` is evaluated on
       ``balance + :amount`` so the persisted level always matches
       the freshly-written balance regardless of any concurrent
       award_xp on the same row.

    Returns {"old_level", "new_level", "leveled_up"} for caller to react
    to level-up. ``new_level`` is read back from ``RETURNING level`` so
    it reflects the persisted truth. ``old_level`` is captured via a
    non-locking pre-UPDATE read — best-effort only : two simultaneous
    awards may both report `leveled_up=true` (FE may show two animations)
    but the persisted state is correct. XP is never debited — amount
    must be positive.
    """
    amount = int(amount)
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    if reason not in VALID_XP_REASONS:
        raise ValueError(f"Invalid XP reason: {reason!r}")

    if apply_streak_multiplier:
        if community_multiplier is None:
            from repositories.challenge_repository import get_active_community_multiplier

            community_multiplier = get_active_community_multiplier(db, user_id, "xp")
        streak_mult = _get_streak_multiplier(db, user_id)
        total_mult = streak_mult + community_multiplier
        if total_mult > 0:
            amount = max(1, int(amount * (1 + total_mult)))

    db.execute(
        text(
            "INSERT INTO xp_transactions "
            "    (id, user_id, amount, reason, reference_id, reference_type) "
            "VALUES (:id, :uid, :amount, :reason, :ref_id, :ref_type)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "amount": amount,
            "reason": reason,
            "ref_id": reference_id,
            "ref_type": reference_type,
        },
    )

    # Best-effort pre-UPDATE level for the `leveled_up` signal. Read
    # without lock — on a race, the worst case is reporting a stale
    # `old_level` for one of two interleaved grants, which only
    # affects the FE level-up animation (cosmetic).
    old_row = db.execute(
        text("SELECT level FROM user_xp_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    old_level = old_row.level if old_row else 0

    # Atomic UPSERT — `level` is recomputed from the post-UPDATE
    # balance directly inside the SQL. Formula mirrors `_compute_level`
    # exactly :
    #     level = max(floor(log(2.0, balance/level_base + 1)), 0)
    # `GREATEST(..., 0)` is defensive : log2 is 0 at x=1 (balance=0),
    # negative for x<1 (impossible in practice — balance is monotone
    # non-decreasing and amount > 0), so the floor never lands below
    # 0 ; the GREATEST hedge keeps the contract even if a future
    # caller seeds the row at balance=0.
    new_level_row = db.execute(
        text(
            "INSERT INTO user_xp_balance (user_id, balance, level, updated_at) "
            "VALUES ("
            "    :uid, "
            "    :amount, "
            "    GREATEST("
            "        floor("
            "            log("
            "                2.0, "
            "                CAST(:amount AS numeric) / CAST(:base AS numeric) + 1"
            "            )"
            "        )::int, "
            "        0"
            "    ), "
            "    now()"
            ") "
            "ON CONFLICT (user_id) DO UPDATE "
            "SET balance    = user_xp_balance.balance + :amount, "
            "    level      = GREATEST("
            "                     floor("
            "                         log("
            "                             2.0, "
            "                             CAST(user_xp_balance.balance + :amount AS numeric) "
            "                             / CAST(:base AS numeric) + 1"
            "                         )"
            "                     )::int, "
            "                     0"
            "                 ), "
            "    updated_at = now() "
            "RETURNING level"
        ),
        {"uid": user_id, "amount": amount, "base": level_base},
    ).first()
    # INSERT ... ON CONFLICT DO UPDATE always affects exactly one row, and
    # RETURNING yields that row — .first() is never None here.
    assert new_level_row is not None  # post-condition of the upsert + RETURNING
    new_level = new_level_row.level
    return {"old_level": old_level, "new_level": new_level, "leveled_up": new_level > old_level}
