"""
Stripe payout client — initiate a bank transfer for cashback withdrawal.

Sandbox mode: if PAYMENT_PROVIDER_KEY is not set, returns a deterministic
fake payout reference (sandbox-<withdrawal_id>). No real API call is made.
This mirrors the gift card sandbox pattern (see gift_card_service.py).

⚠️  When implementing real Stripe: use Stripe Payouts API (POST /v1/payouts).
Requires a connected account with a bank account linked. The `transfer_id`
returned by Stripe is stored as payment_provider_ref + provider_initiated_at.
"""

from __future__ import annotations

import logging
import os
import uuid

log = logging.getLogger(__name__)


class PayoutError(Exception):
    """Raised when the payment provider call fails (non-sandbox mode)."""


def initiate_payout(withdrawal_id: uuid.UUID, amount: int) -> str:
    """
    Initiate a bank transfer for a cashback withdrawal.

    Args:
        withdrawal_id: UUID of the cashback_withdrawals row (used as idempotency key).
        amount: Amount in integer centimes. Must be > 0 — validated by the caller (route/batch).

    Returns:
        payment_provider_ref — Stripe payout ID in prod, or 'sandbox-<withdrawal_id>'
        in sandbox mode (PAYMENT_PROVIDER_KEY not set).

    Raises:
        PayoutError: if the Stripe call fails (non-sandbox mode only).
    """
    api_key = os.environ.get("PAYMENT_PROVIDER_KEY", "")

    if not api_key:
        ref = f"sandbox-{withdrawal_id}"
        log.warning(
            "initiate_payout: SANDBOX mode — no PAYMENT_PROVIDER_KEY (withdrawal_id=%s, amount=%d centimes, ref=%s)",
            withdrawal_id,
            amount,
            ref,
        )
        return ref

    # -----------------------------------------------------------------------
    # Real Stripe implementation — to wire when PAYMENT_PROVIDER_KEY is set
    # -----------------------------------------------------------------------
    import httpx  # lazy import — not required in sandbox

    amount_euros = round(amount / 100, 2)
    try:
        resp = httpx.post(
            "https://api.stripe.com/v1/payouts",
            data={
                "amount": amount,  # Stripe uses centimes for EUR
                "currency": "eur",
                "method": "standard",
                "metadata[withdrawal_id]": str(withdrawal_id),
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Idempotency-Key": str(withdrawal_id),
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        payout_id: str = resp.json()["id"]
        log.info(
            "initiate_payout: Stripe payout created (withdrawal_id=%s, amount=%.2f€, payout_id=%s)",
            withdrawal_id,
            amount_euros,
            payout_id,
        )
        return payout_id
    except httpx.HTTPStatusError as exc:
        log.error(
            "initiate_payout: Stripe HTTP %d for withdrawal %s — %s",
            exc.response.status_code,
            withdrawal_id,
            exc,
        )
        raise PayoutError(f"Stripe HTTP {exc.response.status_code}") from exc
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.error("initiate_payout: network error for withdrawal %s: %s", withdrawal_id, exc)
        raise PayoutError("network error") from exc
    except Exception as exc:
        log.error(
            "initiate_payout: unexpected error for withdrawal %s: %s",
            withdrawal_id,
            exc,
            exc_info=True,
        )
        raise PayoutError("unexpected error") from exc
