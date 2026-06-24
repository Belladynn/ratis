"""Boutique V1 service — orchestrate the user-initiated gift-card order.

Business contract (cf ``ARCH_boutique.md`` § Logique interne) :

1. Validate denomination ∈ allowed set + brand active
2. Validate user CAB balance ≥ cab_cost (atomic UPDATE in cab_repository)
3. Acquire ``pg_advisory_xact_lock`` per-user to serialise concurrent
   gift-card orders (DAS2 fiscal cap protection, audit F-RW-3)
4. Enforce daily / weekly / annual caps (Europe/Paris cutoff)
5. Anti-double-tap idempotency window (60 s default)
6. Atomic transaction :
     - debit_cab → INSERT cabecoin_transactions debit + UPDATE balance
     - INSERT gift_card_orders (status=pending, source_type='shop_purchase',
       source_ref_id = cabecoin_transactions.id)
     (NOTE: users.gift_card_redeemed_ytd_cents is NOT bumped here — the
      authoritative increment happens at issuance time via
      reserve_gift_card_cap; see Task 5 / audit H4.)
7. Schedule Runa fire-and-forget (issue_gift_card_bg) AFTER db.commit()

The function raises :class:`InsufficientBalance` /
:class:`ratis_core.exceptions.NotFound` /
:class:`ratis_core.exceptions.Conflict` /
:class:`ratis_core.exceptions.UnprocessableEntity` for failure modes —
the route translates each to the proper HTTP status (402/404/409/400).

KP-08 — `'gift_card_purchase'` reason is registered in three places:
the DB CHECK constraint (migration 20260508_2200_boutique_v1), the ORM
``_CAB_REASONS`` tuple and the ``VALID_REASONS`` frozenset in
:mod:`repositories.cab_repository`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ratis_core.exceptions import Conflict, NotFound, UnprocessableEntity
from ratis_core.settings import load_settings
from repositories import boutique_repository as repository
from repositories.cab_repository import (
    InsufficientBalance,
    debit_cab,
    get_balance,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.gift_card_service import issue_gift_card_bg  # noqa: F401 — re-exported for tests

log = logging.getLogger(__name__)


_REASON = "gift_card_purchase"


def _settings() -> dict[str, Any]:
    """Return the boutique sub-section of ratis_settings.

    Fail-fast if missing so a misconfigured deploy surfaces at the first
    request rather than silently zero-ing the caps.
    """
    cfg = load_settings()
    if "boutique" not in cfg:
        raise RuntimeError(
            "Settings missing 'boutique' section — aborting. Check app_settings table or ratis_settings.json."
        )
    return cfg["boutique"]


def get_catalog(db: Session) -> dict[str, Any]:
    """Return the boutique catalogue : active brands + allowed denominations
    + ratio. Used by ``GET /rewards/gift-cards/catalog``.
    """
    s = _settings()
    brands = repository.get_active_brands(db)
    return {
        "brands": [
            {
                "id": str(b["id"]),
                "name": b["name"],
                "logo_url": b["logo_url"],
            }
            for b in brands
        ],
        "allowed_denominations_cents": list(s["allowed_denominations_cents"]),
        "ratio_cab_per_eur": int(s["ratio_cab_per_eur"]),
        "cap_per_card_cents": int(s["cap_per_card_cents"]),
        "cap_daily_cents": int(s["cap_daily_cents"]),
        "cap_weekly_cents": int(s["cap_weekly_cents"]),
    }


def create_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    brand_id: uuid.UUID,
    denomination_cents: int,
) -> dict[str, Any]:
    """Create a boutique order, debit CAB and queue the Runa issuance.

    Returns a serialisable dict ready for the route's JSON response.

    Raises :
        UnprocessableEntity('invalid_denomination')
        NotFound('brand_not_available')
        InsufficientBalance — translated to 402 by the caller
        Conflict('daily_redeem_cap_reached' | 'weekly_redeem_cap_reached'
                 | 'annual_gift_card_cap_reached' | 'duplicate_order_recent')
    """
    s = _settings()
    allowed = {int(d) for d in s["allowed_denominations_cents"]}
    cap_per_card = int(s["cap_per_card_cents"])
    cap_daily = int(s["cap_daily_cents"])
    cap_weekly = int(s["cap_weekly_cents"])
    cap_annual = int(s["cap_annual_cents"])
    ratio = int(s["ratio_cab_per_eur"])
    dedup_window = int(s["duplicate_order_window_seconds"])

    # 1. Validate denomination.
    if denomination_cents not in allowed:
        raise UnprocessableEntity("invalid_denomination")
    if denomination_cents > cap_per_card:
        # Belt-and-braces : the per-card cap should already exclude any
        # denomination above it ; if a misconfigured settings file lists
        # a 100 € denomination but a 50 € per-card cap, we still reject.
        raise UnprocessableEntity("invalid_denomination")

    # 2. Validate brand active.
    brand = repository.get_brand_if_active(db, brand_id)
    if brand is None:
        raise NotFound("brand_not_available")

    # 3. CAB cost.
    cab_cost = (denomination_cents // 100) * ratio  # cents → € → CAB

    # 4. Anti-double-tap (cheapest check first — fast SELECT).
    dup = repository.find_recent_duplicate_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination_cents=denomination_cents,
        window_seconds=dedup_window,
    )
    if dup is not None:
        raise Conflict("duplicate_order_recent")

    # 5. CAB balance — fast pre-check (the atomic UPDATE in debit_cab is
    # the actual gate, but the pre-check returns 402 early without any
    # write).
    balance = get_balance(db, user_id)
    if balance < cab_cost:
        raise InsufficientBalance("insufficient_cab_balance")

    # 5.5 Advisory transaction lock per-user — serialise concurrent
    # gift-card orders by the same user across processes / DB connections.
    # Without this, two concurrent requests can both pass the
    # daily / weekly / annual cap reads below before either commits and
    # both INSERT — overshooting the DAS2 fiscal cap (audit F-RW-3).
    # Pattern mirrors KP-41 (handle_barcode_rescan). The lock is
    # auto-released at end-of-transaction (commit OR rollback).
    #
    # The key ``gift_card_cap:{user_id}`` is SHARED with the issuance-time
    # ``reserve_gift_card_cap`` call in gift_card_cap_service — both
    # serialise on the same per-user lock (Task 5, audit H4). This means
    # a create_order and a concurrent issuance for the same user are
    # mutually exclusive, preventing the fast-fail cap reads here from
    # racing with the authoritative YTD increment at issuance. This path
    # is a FAST-FAIL GUARD only: the authoritative increment of
    # users.gift_card_redeemed_ytd_cents happens at issuance via
    # reserve_gift_card_cap, not here.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"gift_card_cap:{user_id}"},
    )

    # 6. Daily cap.
    today = repository.count_redeemed_today_cents(db, user_id)
    if today + denomination_cents > cap_daily:
        raise Conflict("daily_redeem_cap_reached")

    # 7. Weekly cap.
    week = repository.count_redeemed_this_week_cents(db, user_id)
    if week + denomination_cents > cap_weekly:
        raise Conflict("weekly_redeem_cap_reached")

    # 8. Annual cap (denormalised on users).
    ytd = repository.get_user_ytd_cents(db, user_id)
    if ytd + denomination_cents > cap_annual:
        raise Conflict("annual_gift_card_cap_reached")

    # 9. Atomic transaction — debit CAB → INSERT order. The session is the
    # unit of atomicity ; route does db.commit() only on success. If any
    # step raises we re-raise so the route rolls back.
    #
    # NOTE (Task 5, audit H4): users.gift_card_redeemed_ytd_cents is NO
    # LONGER bumped here. The authoritative increment happens at issuance
    # time inside reserve_gift_card_cap (gift_card_cap_service). Bumping
    # it at create_order time was a DOUBLE-COUNT once Task 2-3 added the
    # issuance-time reservation. The cap checks above (steps 6/7/8) are
    # a fast-fail guard only — they read the counter but do not write it.
    #
    # ``debit_cab`` is the canonical CAB-debit helper : atomic
    # ``WHERE balance >= :amount`` UPDATE + ``cabecoin_transactions``
    # INSERT + reason/reference_type validation. We pre-allocate the
    # transaction id and pass it via ``tx_id`` so the gift-card order's
    # ``source_ref_id`` stays a stable link to the debit transaction
    # (audit RW-05 — this path used to re-implement debit_cab inline,
    # bypassing the VALID_REASONS guard).
    cab_tx_id = uuid.uuid4()
    try:
        debit_cab(
            db,
            user_id,
            cab_cost,
            _REASON,
            tx_id=cab_tx_id,
        )
        order_id = repository.insert_order(
            db,
            user_id=user_id,
            brand_id=brand_id,
            denomination_cents=denomination_cents,
            source_ref_id=str(cab_tx_id),
        )
    except InsufficientBalance:
        # Race-conditioned : balance dropped between the pre-check and the
        # atomic UPDATE inside debit_cab. Re-raise the canonical exception
        # message the route maps to 402.
        raise InsufficientBalance("insufficient_cab_balance")
    except Exception:
        # Caller will db.rollback() — re-raise to abort.
        raise

    new_balance = balance - cab_cost
    return {
        "order_id": str(order_id),
        "brand": brand["name"],
        "denomination_cents": denomination_cents,
        "cab_cost": cab_cost,
        "new_cab_balance": new_balance,
        "status": "pending",
        "estimated_arrival": "in a few seconds",
    }
