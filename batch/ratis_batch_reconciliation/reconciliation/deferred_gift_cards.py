# batch/ratis_batch_reconciliation/reconciliation/deferred_gift_cards.py
"""
Re-issuance of deferred gift-card orders (audit H4).

When ``issue_gift_card`` defers an over-cap *earned* reward it leaves the
order ``status='pending'`` with ``eligible_at`` set to next 1 Jan (the date
the annual fiscal cap resets).  ``ratis_batch_referral_payout`` handles the
``referral_reward`` variant (it needs the extra churn check).  This job
handles all other non-referral deferred orders — currently:

  * ``annual_subscription``   — subscription gift card
  * ``battlepass_milestone``  — battlepass reward

For each eligible order (``eligible_at <= now()``), re-trigger issuance by
POSTing to the same internal endpoint used by ``ratis_batch_referral_payout``
(``POST /rewards/gift-cards/{order_id}/issue`` via REWARDS_BASE_URL +
``Authorization: Bearer <INTERNAL_API_KEY>``).  One failure never aborts the
rest of the batch.

⚠️  CRON ORDERING — on 1 Jan this job must run AFTER ``ratis_batch_annual_reset``
has zeroed ``users.gift_card_redeemed_ytd_cents``. Otherwise the re-issued
order is still over the (un-reset) annual cap and ``issue_gift_card`` re-defers
it to the *following* 1 Jan. The schedules guarantee this : annual_reset is
``0 0 1 1 *`` (00:00 UTC) and reconciliation is ``0 6 * * *`` (06:00 UTC).
Keep that ordering if either cron is ever changed.

Returns the count of orders re-triggered (or detected in dry-run).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from reconciliation.rewards_http import notify_rewards_to_issue

log = logging.getLogger(__name__)


def reconcile_deferred_gift_card_orders(db: Session, dry_run: bool = False) -> int:
    """Re-issue eligible deferred non-referral gift-card orders.

    Queries ``gift_card_orders`` for ``status='pending'`` rows where
    ``source_type <> 'referral_reward'``, ``eligible_at IS NOT NULL``, and
    ``eligible_at <= now()``.  For each one (unless ``dry_run``), POSTs to
    the internal issuance endpoint so that ratis_rewards handles the
    provider-side complexity.

    Returns the number of orders re-triggered (or that would be, in dry-run).
    """
    rows = db.execute(
        text(
            "SELECT id FROM gift_card_orders "
            "WHERE status = 'pending' "
            "  AND source_type <> 'referral_reward' "
            "  AND eligible_at IS NOT NULL "
            "  AND eligible_at <= now() "
            "ORDER BY eligible_at"
        )
    ).fetchall()

    count = len(rows)
    log.info(
        "reconcile_deferred_gift_card_orders: %d deferred non-referral order(s) eligible",
        count,
    )

    if dry_run or count == 0:
        return count

    triggered = 0
    for row in rows:
        order_id = str(row.id)
        try:
            ok = notify_rewards_to_issue(order_id)
            if ok:
                triggered += 1
                log.info(
                    "reconcile_deferred_gift_card_orders: re-issued order %s",
                    order_id,
                )
            else:
                log.error(
                    "reconcile_deferred_gift_card_orders: failed to re-issue order %s",
                    order_id,
                )
        except Exception:
            log.error(
                "reconcile_deferred_gift_card_orders: unexpected error for order %s",
                order_id,
                exc_info=True,
            )

    return triggered
