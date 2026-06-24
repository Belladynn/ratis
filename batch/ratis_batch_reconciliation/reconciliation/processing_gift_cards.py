# batch/ratis_batch_reconciliation/reconciliation/processing_gift_cards.py
"""
Re-poll of gift-card orders stuck on Runa-side PROCESSING (audit H4).

When ``issue_gift_card`` POSTs to Runa and Runa answers ``PROCESSING``,
the order is left ``status='pending'`` — the issuance is not yet done
provider-side. The original code comment promised "batch reconciliation
handles re-poll", but no such job existed :

  * ``reconcile_pending_gift_card_orders`` (gift_cards.py) only handles
    ``source_type='shop_purchase'`` orders (fails + refunds after 24h).
  * ``reconcile_deferred_gift_card_orders`` (deferred_gift_cards.py) only
    handles orders with ``eligible_at IS NOT NULL`` (over-cap deferrals).

A non-shop order (``annual_subscription``, ``battlepass_milestone``)
left PROCESSING by Runa has ``eligible_at`` NULL and was never re-polled —
it stayed ``pending`` forever, and since audit H4 it also held its
fiscal-cap reservation (``cap_reserved_cents`` + ``users.gift_card_
redeemed_ytd_cents``) indefinitely, eroding the user's 1199 €/year cap.

This job re-triggers issuance for each genuinely-PROCESSING order by
POSTing to the internal endpoint ``POST /rewards/gift-cards/{id}/issue``
(same path used by ``reconcile_deferred_gift_card_orders``). On the
PROCESSING response the issuance code stored NO provider handle, so a
real Runa GET-status poll is impossible — re-triggering ``issue_gift_card``
is the clean path : it re-reads the order under a per-order
``pg_advisory_xact_lock``, ``reserve_gift_card_cap`` is idempotent on
``cap_reserved_cents > 0``, and Runa's ``idempotency_key`` (``str(order_id)``)
guarantees the re-POST never double-issues. One failure never aborts the
rest of the batch.

Returns the count of orders re-triggered (or detected in dry-run).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from reconciliation.rewards_http import notify_rewards_to_issue

log = logging.getLogger(__name__)

# Grace period before a still-pending non-shop order is considered
# genuinely stuck on Runa PROCESSING. Short enough to release the
# fiscal-cap reservation promptly, long enough that an order mid-issuance
# (background task still running) is never disturbed.
_PROCESSING_STUCK_INTERVAL = "1 hour"


def reconcile_processing_gift_card_orders(db: Session, dry_run: bool = False) -> int:
    """Re-trigger issuance for non-shop gift-card orders stuck on Runa PROCESSING.

    Queries ``gift_card_orders`` for ``status='pending'`` rows where
    ``source_type <> 'shop_purchase'`` (those are handled by
    ``reconcile_pending_gift_card_orders``), ``eligible_at IS NULL``
    (a non-null ``eligible_at`` means an over-cap deferral handled by
    ``reconcile_deferred_gift_card_orders``), and ``created_at`` older
    than the stuck-grace interval (so a freshly-created order whose
    background issuance task is still running is left alone).

    For each one (unless ``dry_run``), POSTs to the internal issuance
    endpoint so ratis_rewards re-calls Runa. ``issue_gift_card`` is
    idempotent, so this is safe to repeat run after run.

    Returns the number of orders re-triggered (or that would be, in dry-run).
    """
    rows = db.execute(
        text(
            "SELECT id FROM gift_card_orders "
            "WHERE status = 'pending' "
            "  AND source_type <> 'shop_purchase' "
            "  AND eligible_at IS NULL "
            "  AND created_at < now() - CAST(:stuck_interval AS interval) "
            "ORDER BY created_at"
        ),
        {"stuck_interval": _PROCESSING_STUCK_INTERVAL},
    ).fetchall()

    count = len(rows)
    log.info(
        "reconcile_processing_gift_card_orders: %d order(s) stuck on Runa PROCESSING",
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
                    "reconcile_processing_gift_card_orders: re-triggered order %s",
                    order_id,
                )
            else:
                log.error(
                    "reconcile_processing_gift_card_orders: failed to re-trigger order %s",
                    order_id,
                )
        except Exception:
            log.error(
                "reconcile_processing_gift_card_orders: unexpected error for order %s",
                order_id,
                exc_info=True,
            )

    return triggered
