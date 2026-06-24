# batch/ratis_batch_reconciliation/reconciliation/rewards_http.py
"""
Shared HTTP helper — trigger gift-card issuance on ratis_rewards.

Several reconciliation jobs need to re-trigger issuance of a stuck
``gift_card_orders`` row by POSTing to the internal endpoint
``POST /rewards/gift-cards/{order_id}/issue`` (REWARDS_BASE_URL +
``Authorization: Bearer <INTERNAL_API_KEY>``) :

  * ``reconcile_deferred_gift_card_orders``   — over-cap deferred orders
  * ``reconcile_processing_gift_card_orders`` — Runa-PROCESSING orders

This was previously duplicated as a private ``_notify_rewards_to_issue``
in ``deferred_gift_cards.py``. Factored here so there is one canonical
implementation (R18 — never duplicate). Mirrors ``notify_rewards_to_issue``
in ``ratis_batch_referral_payout`` exactly — same endpoint, auth, env vars.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)


def notify_rewards_to_issue(order_id: str) -> bool:
    """POST to ratis_rewards to (re-)issue a gift-card order.

    ``issue_gift_card`` is safe to re-trigger : it re-reads the order
    status under a per-order ``pg_advisory_xact_lock`` and returns early
    if the order is no longer ``pending``, and Runa's ``idempotency_key``
    is ``str(order_id)`` — so a re-POST never double-issues.

    Returns True on success, False on any error (logged here).
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
