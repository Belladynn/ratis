"""
Referral payout batch — daily cron.

Processes ``gift_card_orders`` rows with ``source_type='referral_reward'``
that have reached their ``eligible_at`` deadline (~30 days after the
referred user subscribed). For each eligible order :

1. Verify the referred user (Y) is **still actively subscribed**.
   - If yes  → mark the order ``status='eligible'`` and enqueue the actual
                 gift-card issuance by notifying ratis_rewards (which calls
                 Runa's API in a BackgroundTask).
   - If no   → mark the order ``status='churned'`` (permanent — the reward
                 is NOT issued, protects Ratis from churn-farming).

The service call to ratis_rewards is via a lightweight HTTP POST to a
dedicated endpoint (``POST /rewards/gift-cards/issue``) which accepts the
order_id and kicks off the Runa call. This keeps the batch itself free of
Runa-API bindings — all provider code stays in ratis_rewards.

Usage :
  uv run python batch/ratis_batch_referral_payout/payout.py
  uv run python batch/ratis_batch_referral_payout/payout.py --dry-run

Env vars required :
  DATABASE_URL           — direct DB access
  REWARDS_BASE_URL       — for the internal HTTP notify (optional dry-run skips)
  INTERNAL_API_KEY       — ditto
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass

import httpx
from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.startup import require_env
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("referral_payout")

BATCH_NAME = "referral_payout"


@dataclass(frozen=True)
class EligibleOrder:
    order_id: str
    referrer_user_id: str
    referral_use_id: str
    referred_user_id: str


def fetch_eligible_orders(db: Session) -> list[EligibleOrder]:
    """
    Return all pending referral gift-card orders whose ``eligible_at`` has
    passed — candidates for issuance or churn cancellation.

    Joins to ``referral_uses`` to expose the filleul id for the subscription
    recency check performed by the caller.
    """
    rows = db.execute(
        text(
            """
            SELECT  gco.id            AS order_id,
                    gco.user_id       AS referrer_user_id,
                    ru.id             AS referral_use_id,
                    ru.referred_user_id
            FROM gift_card_orders gco
            JOIN referral_uses ru ON ru.id::text = gco.source_ref_id
            WHERE gco.source_type = 'referral_reward'
              AND gco.status = 'pending'
              AND gco.eligible_at IS NOT NULL
              AND gco.eligible_at <= now()
            ORDER BY gco.created_at ASC
            """
        )
    ).fetchall()
    return [
        EligibleOrder(
            order_id=str(r.order_id),
            referrer_user_id=str(r.referrer_user_id),
            referral_use_id=str(r.referral_use_id),
            referred_user_id=str(r.referred_user_id),
        )
        for r in rows
    ]


def is_still_subscribed(db: Session, referred_user_id: str) -> bool:
    """
    Check if the referred user still has an active subscription.

    Matches the same criteria as ``referral_service._get_active_plan`` :
    a row in ``subscriptions`` with ``status='active'`` and plan in
    {monthly, annual}.
    """
    plan = db.execute(
        text(
            """
            SELECT plan FROM subscriptions
            WHERE user_id = :uid AND status = 'active'
            ORDER BY started_at DESC LIMIT 1
            """
        ),
        {"uid": referred_user_id},
    ).scalar()
    return plan in ("monthly", "annual")


def mark_churned(db: Session, order_id: str) -> None:
    """Flag a churned referral order as ``status='churned'`` — reward never issued.

    The order moves to the dedicated ``'churned'`` terminal status so that
    anti-fraud / fiscal audits can distinguish churn cancellation from a real
    Runa issuance failure (``'failed'``). ``failed_at`` is reused as the
    terminal-timestamp column (adding a new column is out of scope for this
    fix). The ``WHERE id = :oid AND status = 'pending'`` guard makes the
    operation idempotent.
    """
    db.execute(
        text(
            "UPDATE gift_card_orders SET status = 'churned', failed_at = now() WHERE id = :oid AND status = 'pending'"
        ),
        {"oid": order_id},
    )


def notify_rewards_to_issue(order_id: str) -> bool:
    """
    Tell ratis_rewards to actually call Runa and issue the card.

    Fire-and-forget HTTP POST to the internal endpoint — rewards handles
    the provider-side complexity (retry, webhook reconciliation).

    Returns True on success, False on any error (logged).
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    if not base_url or not key:
        log.error(
            "notify_rewards_to_issue: REWARDS_BASE_URL or INTERNAL_API_KEY "
            "missing — cannot trigger issuance for order=%s",
            order_id,
        )
        return False

    url = f"{base_url}/rewards/gift-cards/{order_id}/issue"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        log.error(
            "notify_rewards_to_issue: HTTP %d for order=%s",
            exc.response.status_code,
            order_id,
        )
        return False
    except Exception as exc:
        log.warning(
            "notify_rewards_to_issue: network error for order=%s: %s",
            order_id,
            exc,
        )
        return False


def run(session_factory, dry_run: bool = False) -> dict:
    """
    Main entry point. Iterates over eligible orders, verifies subscription
    recency, then either marks churned (terminal) or notifies rewards to
    issue (via HTTP POST).

    Returns a stats dict : ``{"candidates": N, "issued": I, "churned": C,
    "errors": E, "dry_run": bool}``.
    """
    stats = {"candidates": 0, "issued": 0, "churned": 0, "errors": 0, "dry_run": dry_run}

    with session_factory() as db:
        orders = fetch_eligible_orders(db)
        stats["candidates"] = len(orders)
        log.info("referral_payout: %d candidate(s) found", len(orders))

        for order in orders:
            still_subbed = is_still_subscribed(db, order.referred_user_id)

            if not still_subbed:
                log.info(
                    "referral_payout: order=%s — referred_user=%s churned → mark churned",
                    order.order_id,
                    order.referred_user_id,
                )
                if not dry_run:
                    mark_churned(db, order.order_id)
                stats["churned"] += 1
                continue

            log.info(
                "referral_payout: order=%s — referred_user=%s still subscribed → notify issue",
                order.order_id,
                order.referred_user_id,
            )
            if dry_run:
                stats["issued"] += 1
                continue

            ok = notify_rewards_to_issue(order.order_id)
            if ok:
                stats["issued"] += 1
            else:
                stats["errors"] += 1

        if not dry_run:
            db.commit()

    log.info("referral_payout: %s", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Referral payout batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen, no DB mutations, no HTTP notifies.",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during the payout run is then captured.
    init_sentry("ratis_batch_referral_payout")

    if not args.dry_run:
        require_env("DATABASE_URL", "REWARDS_BASE_URL", "INTERNAL_API_KEY")
    else:
        require_env("DATABASE_URL")

    engine = make_engine(os.environ["DATABASE_URL"])
    session_factory = sessionmaker(engine)
    try:
        run(session_factory, dry_run=args.dry_run)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
