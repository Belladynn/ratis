"""
Gift card service — Runa provider integration.

issue_gift_card(order_id, db) :
  - Fetches the order + brand from DB
  - POSTs to Runa API
  - Updates order status (issued / failed / stays pending on PROCESSING)
  - Never raises — all errors are logged and swallowed

The function is designed to run as a FastAPI BackgroundTask.
For background task usage, call the wrapper _issue_gift_card_bg(order_id)
which creates its own DB session from DATABASE_URL.
"""

from __future__ import annotations

import logging
import os
import uuid

import httpx
import sentry_sdk
from repositories.cab_repository import award_cab
from repositories.gift_card_repository import get_order, update_order_failed, update_order_issued
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_RUNA_BASE_URL = "https://api.runa.io/v1"

# Cached at module load — env doesn't change at runtime, and the lifespan
# in main.py already enforces ``require_env("DATABASE_URL")`` so this read
# is guaranteed to find a non-empty value in any context that successfully
# imports this module (uvicorn boot or pytest conftest). Reading per-call
# inside ``issue_gift_card_bg`` was wasted work (audit F-9).
#
# ``GIFT_CARD_PROVIDER_KEY`` is intentionally NOT cached here — the test
# ``test_issue_gift_card_no_api_key_sandbox`` uses ``monkeypatch.delenv``
# at runtime to exercise the sandbox fallback path. Caching at import
# would make that test (and the documented runtime sandbox toggle)
# impossible to express.
_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def issue_gift_card(order_id: uuid.UUID, db: Session) -> None:
    """
    Call Runa to issue the gift card for the given order.

    Status mapping:
      COMPLETE   → issued  (code + provider_order_id stored)
      PROCESSING → pending (batch reconciliation will re-poll)
      FAILED     → failed

    If GIFT_CARD_PROVIDER_KEY is not set, runs in sandbox mode:
    simulates a COMPLETE response with a fake code for testing purposes.
    Never raises.

    Concurrency (audit RW-money F-1) : a per-order ``pg_advisory_xact_lock``
    serialises two issuances of the same order — two ``POST /issue`` calls,
    a referral-payout retry racing the original, two annual-subscription
    background tasks. The lock is acquired FIRST ; the status is then
    re-read under it and the function returns early if the order is no
    longer ``pending`` — so Runa is called at most once per order. The
    lock auto-releases at end-of-transaction (commit OR rollback).
    """
    from sqlalchemy import text

    api_key = os.environ.get("GIFT_CARD_PROVIDER_KEY", "")

    # Serialise concurrent issuances of THIS order. Pattern mirrors
    # boutique_service.create_order (KP-41). Held until the caller's
    # transaction ends.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"gift_card_issue:{order_id}"},
    )

    order = get_order(db, order_id)
    if not order:
        log.error("issue_gift_card: order %s not found", order_id)
        return

    # Idempotence gate — re-read under the advisory lock. A concurrent
    # issuance that already drove this order to a terminal state
    # (issued / failed) means we must NOT call Runa again.
    if order["status"] != "pending":
        log.info(
            "issue_gift_card: order %s already in status %r — skipping (concurrent issuance already handled it)",
            order_id,
            order["status"],
        )
        return

    # --- Fiscal cap reservation (audit H4) -------------------------------
    # Reserve the order's denomination against the user's 1199 € annual cap
    # BEFORE the Runa call (a DB lock cannot be held across the HTTP call).
    # Earned rewards over the cap are deferred to next year ; the boutique
    # is hard-blocked (its CAB is refunded by _mark_failed).
    from services.gift_card_cap_service import reserve_gift_card_cap

    cap_decision = reserve_gift_card_cap(db, order_id, allow_defer=(order["source_type"] != "shop_purchase"))
    if cap_decision.outcome == "defer":
        db.execute(
            text("UPDATE gift_card_orders SET eligible_at = :ts WHERE id = :oid"),
            {"ts": cap_decision.deferred_until, "oid": order_id},
        )
        log.info(
            "issue_gift_card: order %s over annual cap — deferred to %s",
            order_id,
            cap_decision.deferred_until,
        )
        return
    if cap_decision.outcome == "block":
        log.warning(
            "issue_gift_card: order %s (shop_purchase) over annual cap — failing",
            order_id,
        )
        _mark_failed(db, order_id)
        return
    # outcome == "allow" → fall through to issuance

    face_value = round(order["denomination"] / 100, 2)  # centimes → euros float

    # Re-fetch provider_brand_id (not in get_order result — query directly)
    row = db.execute(
        text("SELECT provider_brand_id FROM gift_card_brands WHERE id = :bid"),
        {"bid": order["brand"]["id"]},
    ).first()
    if not row:
        log.error("issue_gift_card: brand %s not found (order=%s)", order["brand"]["id"], order_id)
        _mark_failed(db, order_id)
        return
    provider_brand_id = row.provider_brand_id

    if not api_key:
        fake_code = f"SANDBOX-{str(order_id).upper()[:8]}"
        log.warning(
            "issue_gift_card: SANDBOX mode — no GIFT_CARD_PROVIDER_KEY, simulating COMPLETE (order=%s, fake_code=%s)",
            order_id,
            fake_code,
        )
        update_order_issued(db, order_id, provider_order_id="sandbox", code=fake_code)
        return

    url = f"{_RUNA_BASE_URL}/orders"
    try:
        resp = httpx.post(
            url,
            json={
                "product_id": provider_brand_id,
                "face_value": face_value,
                "currency": "EUR",
                "idempotency_key": str(order_id),
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        log.error(
            "issue_gift_card: Runa HTTP %d (order=%s) — %s",
            exc.response.status_code,
            order_id,
            exc,
        )
        _mark_failed(db, order_id)
        return
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning("issue_gift_card: network error (order=%s): %s", order_id, exc)
        _mark_failed(db, order_id)
        return
    except Exception:
        log.warning("issue_gift_card: unexpected error (order=%s)", order_id, exc_info=True)
        _mark_failed(db, order_id)
        return

    runa_status = data.get("status", "FAILED")

    if runa_status == "COMPLETE":
        update_order_issued(
            db,
            order_id,
            provider_order_id=data["id"],
            code=data["redemption_code"],
        )
        log.info("issue_gift_card: order %s issued (runa_id=%s)", order_id, data["id"])

    elif runa_status == "PROCESSING":
        log.info("issue_gift_card: order %s still PROCESSING — batch will re-poll", order_id)
        # Leave as pending — batch reconciliation handles re-poll

    else:  # FAILED or unknown
        log.warning("issue_gift_card: Runa returned status %r for order %s", runa_status, order_id)
        _mark_failed(db, order_id)


def _refund_order_cab(db: Session, order_id: uuid.UUID) -> None:
    """Credit back the CAB debited for a shop_purchase gift-card order.

    Only shop_purchase orders were paid with CAB — other source types
    (annual_subscription, battlepass_milestone, referral_reward) were not.
    If no matching debit transaction is found the function returns silently
    (defensive — nothing to refund).

    The caller owns the transaction; this function never commits.
    """
    order = get_order(db, order_id)
    if order is None or order["source_type"] != "shop_purchase":
        return

    # Resolve the original debit transaction linked via source_ref_id
    try:
        debit_tx_id = uuid.UUID(order["source_ref_id"])
    except (ValueError, AttributeError):
        log.warning(
            "_refund_order_cab: source_ref_id %r for order %s is not a valid UUID — skipping",
            order["source_ref_id"],
            order_id,
        )
        return

    row = db.execute(
        text("SELECT amount, user_id FROM cabecoin_transactions WHERE id = :tid AND direction = 'debit'"),
        {"tid": debit_tx_id},
    ).first()
    if row is None:
        log.warning(
            "_refund_order_cab: debit transaction %s for order %s not found — skipping",
            debit_tx_id,
            order_id,
        )
        return

    award_cab(
        db,
        row.user_id,
        row.amount,
        reason="gift_card_refund",
        apply_streak_multiplier=False,
        apply_to_bp_progress=False,
    )


def _mark_failed(db: Session, order_id: uuid.UUID) -> None:
    """Transition a pending gift-card order to 'failed', refund the CAB, and
    release the fiscal-cap reservation.

    The status transition (atomic, guarded on status='pending'), the CAB
    refund, and the cap release happen in the SAME transaction — the caller
    commits all three or none. On error, roll back so the order stays
    'pending' (consistent — the reconciliation batch will retry it) and
    alert : a CAB debit with no gift card and no refund is a money-loss event.
    """
    from services.gift_card_cap_service import release_gift_card_cap

    try:
        marked = update_order_failed(db, order_id)
        if marked:
            _refund_order_cab(db, order_id)
            release_gift_card_cap(db, order_id)
    except Exception:
        db.rollback()
        log.error(
            "issue_gift_card: CRITICAL — could not fail+refund order %s (order stuck pending, CAB not refunded)",
            order_id,
            exc_info=True,
        )
        sentry_sdk.capture_message(
            f"gift-card order {order_id}: fail+refund failed — CAB debit unrefunded",
            level="error",
        )


def issue_gift_card_bg(order_id: uuid.UUID) -> None:
    """
    Background task wrapper — creates its own DB session.
    Use with FastAPI BackgroundTasks: background_tasks.add_task(issue_gift_card_bg, order_id)
    """
    from ratis_core.database import make_engine
    from sqlalchemy.orm import sessionmaker

    if not _DATABASE_URL:
        log.error("issue_gift_card_bg: DATABASE_URL not set — cannot issue order %s", order_id)
        return

    engine = make_engine(_DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        issue_gift_card(order_id, db)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("issue_gift_card_bg: unexpected error for order %s", order_id, exc_info=True)
    finally:
        db.close()
        engine.dispose()
