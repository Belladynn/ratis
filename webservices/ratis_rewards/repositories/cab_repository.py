"""
CAB (Cabecoins) repository — raw SQL for atomicity.

All write operations (award_cab, debit_cab) work within the caller's session
transaction. The caller is responsible for commit() after all operations succeed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.exceptions import InsufficientBalance
from repositories.streak_repository import get_streak_multiplier as _get_streak_multiplier

# Re-export so existing `from repositories.cab_repository import InsufficientBalance` keeps working.
__all__ = [
    "VALID_REASONS",
    "VALID_REFERENCE_TYPES",
    "InsufficientBalance",
    "admin_adjust_cab",
    "award_cab",
    "debit_cab",
    "get_balance",
]


VALID_REASONS = frozenset(
    {
        "receipt_scan",
        "label_scan",
        # Legacy reason kept accepted for historical rows minted before
        # phase B (PR #325) — the catalogue and runtime now emit
        # "product_identification" instead.
        "barcode_scan",
        "product_identification",  # Phase B — rename of barcode_scan.
        "fill_product_field",  # Phase B — manual product attribute filling.
        "scan_distinct",  # Phase B — diversity badge / mission progress.
        "promo_found",  # Phase B — user-flagged in-store promo.
        "mission_reward",
        "battlepass_milestone",
        "referral",
        "cashback_boost_debit",
        "cashback_boost_refund",
        "shop_purchase",
        "stonks_boost",
        "mission_freeze",
        "food_reserve_purchase",  # Feed Jack — keep in sync with _CAB_REASONS / DA-04
        "streak_repair",  # Feed Jack — keep in sync with _CAB_REASONS / DA-04
        "challenge_milestone",  # Défi communautaire — keep in sync with _CAB_REASONS
        "mystery_product",  # Produit mystère — keep in sync with _CAB_REASONS / DA-04
        "admin_adjustment",  # Admin manual mutation — keep in sync with _CAB_REASONS / KP-08
        "retro_scan",  # ratis_batch_data_reconciliation Job 4 — retroactive CAB on
        # scans newly resolved by Job 1. Keep in sync with _CAB_REASONS / KP-08.
        "gift_card_purchase",  # Boutique V1 debit — keep in sync with _CAB_REASONS / KP-08.
        # Distinct from the legacy 'shop_purchase' reason.
        "gift_card_refund",  # Boutique V1 — CAB refunded when gift-card issuance fails.
        # Keep in sync with _CAB_REASONS / KP-08.
        "achievement_unlock",  # Achievements V1 — CAB granted on unlock.
        # Keep in sync with _CAB_REASONS / KP-08.
    }
)


# Mirrors the live ``cabecoin_transactions_reference_type_check`` CHECK
# constraint (see ratis_core/models/gamification.py CabecoinsTransaction
# __table_args__ and alembic 20260510_1020_add_cab_reason_achievement).
# KP-08 multi-place sync applies — any literal added to the DB CHECK must
# be added here so an unknown reference_type is rejected in Python with a
# clear ValueError rather than surfacing as an opaque 500 IntegrityError
# at COMMIT.
VALID_REFERENCE_TYPES = frozenset(
    {
        "scan",
        "mission",
        "battlepass_milestone",
        "referral",
        "user_mission",
        "community_challenge_milestone",
        "admin",
        "retro_scan",
        "achievement",
    }
)


def _validate_reference_type(reference_type: str | None) -> None:
    """Reject a reference_type the DB CHECK constraint would refuse.

    ``None`` is always valid (not every ledger row carries a reference).
    Raises ValueError — same failure mode as the ``reason`` guard — so the
    caller gets a clear error before any balance mutation instead of an
    opaque IntegrityError at COMMIT.
    """
    if reference_type is not None and reference_type not in VALID_REFERENCE_TYPES:
        raise ValueError(f"Invalid CAB reference_type: {reference_type!r}")


def get_balance(db: Session, user_id: uuid.UUID) -> int:
    """Return the user's current CAB balance (0 if row missing)."""
    row = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    return row.balance if row else 0


def award_cab(
    db: Session,
    user_id: uuid.UUID,
    amount: int,
    reason: str,
    reference_id: uuid.UUID | None = None,
    reference_type: str | None = None,
    apply_streak_multiplier: bool = True,
    community_multiplier: float | None = None,
    apply_to_bp_progress: bool = True,
    active_season_id: uuid.UUID | None = None,
) -> None:
    """
    Credit CAB to a user — atomic within the caller's transaction.

    Steps (all in the same session):
    1. Apply streak multiplier if active (min(streak_days * 5%, 100%))
    2. UPDATE user_cab_balance balance += effective_amount
    3. INSERT cabecoin_transactions (credit, stores effective_amount)
    4. If an active battlepass season exists AND apply_to_bp_progress:
       INSERT/UPDATE user_battlepass_progress

    The streak multiplier is applied automatically when apply_streak_multiplier=True.
    Pass apply_streak_multiplier=False for internal/system credits that should not be boosted
    (e.g. streak_repair refunds — not applicable here since streak_repair is a debit).

    Pass ``apply_to_bp_progress=False`` to skip the season progress UPSERT — used by
    ``claim_milestone`` to prevent a self-feeding loop where claiming a CAB milestone
    would feed back into ``cab_earned_season`` and unlock the next milestone for free.
    Bug archi acted 2026-05-08.

    ``active_season_id`` lets a caller that already fetched the active season
    (e.g. ``events_service.handle_action``) hand it in, so the season progress
    UPSERT skips the redundant ``battlepass_seasons WHERE is_active`` lookup
    (RW-10). When omitted the season is resolved internally as before.

    ⚠️  RECONCILIATION SYNC — batch/ratis_batch_reconciliation replicates this logic
    directly in SQL (no HTTP call). Any change to the steps above (new fields, new
    constraints, new side-effects) must be reflected in:
        batch/ratis_batch_reconciliation/reconciliation/cab.py
    """
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    if reason not in VALID_REASONS:
        raise ValueError(f"Invalid CAB reason: {reason!r}")
    _validate_reference_type(reference_type)

    if apply_streak_multiplier:
        if community_multiplier is None:
            from repositories.challenge_repository import get_active_community_multiplier

            community_multiplier = get_active_community_multiplier(db, user_id, "cab")
        streak_mult = _get_streak_multiplier(db, user_id)
        total_mult = streak_mult + community_multiplier
        if total_mult > 0:
            amount = max(1, int(amount * (1 + total_mult)))
    # user_cab_balance row is guaranteed to exist — ratis_auth creates it at
    # registration (email, Google, Apple flows all call _create_cab_balance).
    db.execute(
        text("UPDATE user_cab_balance SET balance = balance + :amount WHERE user_id = :uid"),
        {"amount": amount, "uid": user_id},
    )
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "    (id, user_id, direction, amount, reason, reference_id, reference_type) "
            "VALUES (:id, :uid, 'credit', :amount, :reason, :ref_id, :ref_type)"
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
    if apply_to_bp_progress:
        season_id = active_season_id
        if season_id is None:
            active_season = db.execute(text("SELECT id FROM battlepass_seasons WHERE is_active = TRUE LIMIT 1")).first()
            season_id = active_season.id if active_season else None
        if season_id is not None:
            db.execute(
                text(
                    "INSERT INTO user_battlepass_progress "
                    "    (id, user_id, season_id, cab_earned_season) "
                    "VALUES (:id, :uid, :sid, :amount) "
                    "ON CONFLICT (user_id, season_id) DO UPDATE "
                    "SET cab_earned_season = "
                    "    user_battlepass_progress.cab_earned_season + :amount"
                ),
                {"id": uuid.uuid4(), "uid": user_id, "sid": season_id, "amount": amount},
            )


def debit_cab(
    db: Session,
    user_id: uuid.UUID,
    amount: int,
    reason: str,
    reference_id: uuid.UUID | None = None,
    reference_type: str | None = None,
    tx_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Debit CAB from a user — atomic within the caller's transaction.

    Raises InsufficientBalance if balance < amount (atomic check via WHERE clause).
    Raises ValueError on a non-positive amount, an unknown ``reason`` or an
    unknown ``reference_type``.

    ``tx_id`` lets the caller pre-allocate the ``cabecoin_transactions`` id —
    used by the boutique flow so the gift-card order's ``source_ref_id`` can
    point at a stable, known transaction id. When omitted a fresh UUID is
    generated. The (generated or supplied) transaction id is returned.
    """
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    if reason not in VALID_REASONS:
        raise ValueError(f"Invalid CAB reason: {reason!r}")
    _validate_reference_type(reference_type)
    result = db.execute(
        text("UPDATE user_cab_balance SET balance = balance - :amount WHERE user_id = :uid AND balance >= :amount"),
        {"amount": amount, "uid": user_id},
    )
    if result.rowcount == 0:
        raise InsufficientBalance("insufficient CAB balance")
    tx_id = tx_id or uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "    (id, user_id, direction, amount, reason, reference_id, reference_type) "
            "VALUES (:id, :uid, 'debit', :amount, :reason, :ref_id, :ref_type)"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "amount": amount,
            "reason": reason,
            "ref_id": reference_id,
            "ref_type": reference_type,
        },
    )
    return tx_id


def admin_adjust_cab(
    db: Session,
    *,
    user_id: uuid.UUID,
    direction: str,
    amount: int,
    operator: str,
    operator_reason: str,
) -> uuid.UUID:
    """Manual admin CAB credit/debit — atomic within the caller's transaction.

    Inserts a row in ``cabecoin_transactions`` with :
        - reason='admin_adjustment'
        - reference_type='admin'
        - reference_id=<uuid4>  (own audit handle)
        - context={operator, reason}

    Then UPDATEs ``user_cab_balance`` atomically.

    Raises :
        - ValueError on bad direction / non-positive amount
        - LookupError if the user_cab_balance row does not exist (unknown user)
        - InsufficientBalance on debit > current balance

    Returns the inserted transaction id.

    No streak / battlepass side-effects — admin tweaks must NOT pollute
    progression metrics.
    """
    if direction not in ("credit", "debit"):
        raise ValueError(f"direction must be 'credit' or 'debit', got {direction!r}")
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")

    if direction == "credit":
        result = db.execute(
            text("UPDATE user_cab_balance SET balance = balance + :amount WHERE user_id = :uid"),
            {"amount": amount, "uid": user_id},
        )
        if result.rowcount == 0:
            raise LookupError(f"user_cab_balance row not found for {user_id}")
    else:  # debit
        # Atomic check : balance >= amount via WHERE clause (R09).
        result = db.execute(
            text("UPDATE user_cab_balance SET balance = balance - :amount WHERE user_id = :uid AND balance >= :amount"),
            {"amount": amount, "uid": user_id},
        )
        if result.rowcount == 0:
            # Distinguish "user missing" from "insufficient balance".
            exists = db.execute(
                text("SELECT 1 FROM user_cab_balance WHERE user_id = :uid"),
                {"uid": user_id},
            ).first()
            if exists is None:
                raise LookupError(f"user_cab_balance row not found for {user_id}")
            raise InsufficientBalance("insufficient CAB balance")

    tx_id = uuid.uuid4()
    ref_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "    (id, user_id, direction, amount, reason, reference_id, "
            "     reference_type, context) "
            "VALUES (:id, :uid, :direction, :amount, 'admin_adjustment', "
            "        :ref_id, 'admin', CAST(:context AS jsonb))"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "direction": direction,
            "amount": amount,
            "ref_id": ref_id,
            "context": json.dumps({"operator": operator, "reason": operator_reason}),
        },
    )
    return tx_id


def get_active_season(db: Session) -> dict[str, Any] | None:
    """Return the active battlepass season or None."""
    row = db.execute(
        text("SELECT id, season_number, name, ends_at FROM battlepass_seasons WHERE is_active = TRUE LIMIT 1")
    ).first()
    if not row:
        return None
    return {
        "id": row.id,
        "season_number": row.season_number,
        "name": row.name,
        "ends_at": row.ends_at,
    }


def get_cab_earned_season(db: Session, user_id: uuid.UUID, season_id: uuid.UUID) -> int:
    """Return CAB earned in the current season (0 if no progress row)."""
    row = db.execute(
        text("SELECT cab_earned_season FROM user_battlepass_progress WHERE user_id = :uid AND season_id = :sid"),
        {"uid": user_id, "sid": season_id},
    ).first()
    return row.cab_earned_season if row else 0


def get_next_milestone_delta(db: Session, user_id: uuid.UUID, season_id: uuid.UUID, cab_earned_season: int) -> int:
    """
    Compute delta to next claimable milestone.

    Returns cab_required_next_milestone - cab_earned_season, or 0 if all milestones claimed.
    """
    # Milestones not yet claimed by this user, ordered by cab_required ascending
    row = db.execute(
        text(
            "SELECT m.cab_required "
            "FROM battlepass_milestones m "
            "WHERE m.season_id = :sid "
            "  AND m.cab_required > :earned "
            "  AND NOT EXISTS ("
            "      SELECT 1 FROM user_battlepass_claims c "
            "      WHERE c.milestone_id = m.id AND c.user_id = :uid"
            "  ) "
            "ORDER BY m.cab_required ASC "
            "LIMIT 1"
        ),
        {"sid": season_id, "uid": user_id, "earned": cab_earned_season},
    ).first()
    if not row:
        return 0
    return row.cab_required - cab_earned_season
