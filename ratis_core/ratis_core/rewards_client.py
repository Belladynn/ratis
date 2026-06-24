"""
Inter-service client for ratis_rewards.

Usage (fire-and-forget, inside a FastAPI BackgroundTask):

    from ratis_core.rewards_client import (
        trigger_action, trigger_referral_reward, trigger_cashback_scan,
    )

    trigger_referral_reward(referred_user_id=user.id, plan="monthly")
    trigger_action(
        user_id=user.id,
        action_type="receipt_scan",
        idempotency_key=str(scan_id),
        context={"scan_id": str(scan_id)},
    )
    trigger_cashback_scan(user_id=user.id, scan_id=scan.id,
                          receipt_lines=[{"ean": "...", "price": 2.50}])

REWARDS_BASE_URL and INTERNAL_API_KEY must be configured in the environment.
Missing config is a silent no-op (warning logged) — use require_env() in the
service lifespan to fail fast at startup rather than silently dropping calls.

REWARDS_BASE_URL **MUST** include the ``/api/v1`` prefix (e.g.
``http://rewards:8004/api/v1`` in compose, ``https://rewards.ratis.app/api/v1``
on Railway). The functions below build paths like ``f"{base}/rewards/..."``
without re-adding the prefix — RW's FastAPI router is mounted at ``/api/v1``,
so a base without the prefix produces 404s that are silently swallowed by
fire-and-forget. See ``ARCH_deployment.md § Cross-service URL conventions``.

The call is best-effort: errors are logged and swallowed so that a reward
trigger failure never crashes the calling service.

Phase B (PR #325) replaced the legacy ``notify_scan_accepted`` helper with
``trigger_action`` — a single generic primitive that carries
``(action_type, qualifier, quantity, idempotency_key, context)``. The
server inserts a ``reward_events`` row keyed on ``idempotency_key`` and
dispatches CAB / XP / mission progress in a single transaction.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid

import httpx

log = logging.getLogger("rewards_client")


def trigger_referral_reward(referred_user_id: uuid.UUID, plan: str) -> None:
    """
    POST /rewards/referral/trigger — fire-and-forget. Never raises.

    Log levels:
    - ERROR : misconfiguration (invalid URL, HTTP 4xx) — action required.
    - WARNING : transient failure (network, HTTP 5xx) — rewards service may be down.
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    uid = hashlib.sha256(str(referred_user_id).encode()).hexdigest()[:8]

    if not base_url or not key:
        log.warning(
            "trigger_referral_reward skipped — REWARDS_BASE_URL or INTERNAL_API_KEY not configured "
            "(referred_user=%s… plan=%s)",
            uid,
            plan,
        )
        return

    url = f"{base_url}/rewards/referral/trigger"
    try:
        resp = httpx.post(
            url,
            json={"referred_user_id": str(referred_user_id), "plan": plan},
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "trigger_referral_reward: invalid REWARDS_BASE_URL %r (referred_user=%s… plan=%s) — check config",
            url,
            uid,
            plan,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            log.error(
                "trigger_referral_reward: HTTP %d (referred_user=%s… plan=%s) — check API key",
                exc.response.status_code,
                uid,
                plan,
            )
        else:
            log.warning(
                "trigger_referral_reward: HTTP %d from rewards (referred_user=%s… plan=%s) — rewards unavailable",
                exc.response.status_code,
                uid,
                plan,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "trigger_referral_reward: network error (referred_user=%s… plan=%s): %s",
            uid,
            plan,
            exc,
        )
    except Exception:
        log.warning(
            "trigger_referral_reward: unexpected error (referred_user=%s… plan=%s)",
            uid,
            plan,
            exc_info=True,
        )


def trigger_action(
    user_id: uuid.UUID,
    action_type: str,
    quantity: int = 1,
    *,
    qualifier: str | None = None,
    idempotency_key: str | None = None,
    context: dict | None = None,
) -> None:
    """
    POST /rewards/events/action — fire-and-forget. Never raises.

    Generic gamification event primitive that supersedes the V0
    ``notify_scan_accepted`` helper. The server records the event in
    ``reward_events`` (keyed on ``idempotency_key``) and dispatches CAB,
    XP and mission progress in one transaction.

    Args :
        user_id : owner of the event.
        action_type : one of ``receipt_scan``, ``label_scan``,
            ``product_identification``, ``fill_product_field``,
            ``scan_distinct``, ``promo_found``, ``price_compared``.
        quantity : how many increments this event represents (≥ 1).
            CAB / XP awarded scale linearly ; mission progress
            increments by ``quantity`` for non-distinct missions, by 1
            per *new distinct* qualifier value for ``scan_distinct``.
        qualifier : prefixed value such as ``attribute:organic``,
            ``category:dairy``, ``store:<uuid>``. ``None`` = no filter
            (matches missions whose qualifier IS NULL plus the
            "type tag" missions, e.g. a ``store`` mission only matches
            events whose qualifier starts with ``store:``).
        idempotency_key : caller-provided key to dedup retries. If
            ``None`` the server synthesises one from
            ``sha256(user_id, action_type, qualifier, scan_id, ts_min)``.
            Whether caller- or server-supplied, it doubles as the
            forensics key for reconciliation.
        context : free dict (scan_id, ean, store_id, …) persisted as
            ``reward_events.payload`` for audit and post-mortem.

    Log levels :
        ERROR — misconfiguration (invalid URL, HTTP 4xx).
        WARNING — transient failure (network, HTTP 5xx).
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    uid = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]

    if not base_url or not key:
        log.warning(
            "trigger_action skipped — REWARDS_BASE_URL or INTERNAL_API_KEY "
            "not configured (user=%s… action=%s qualifier=%s)",
            uid,
            action_type,
            qualifier,
        )
        return

    body: dict = {
        "user_id": str(user_id),
        "action_type": action_type,
        "quantity": quantity,
    }
    if qualifier is not None:
        body["qualifier"] = qualifier
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    if context is not None:
        body["context"] = context

    url = f"{base_url}/rewards/events/action"
    try:
        resp = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "trigger_action: invalid REWARDS_BASE_URL %r (user=%s… action=%s) — check config",
            url,
            uid,
            action_type,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            log.error(
                "trigger_action: HTTP %d (user=%s… action=%s qualifier=%s) — check API key",
                exc.response.status_code,
                uid,
                action_type,
                qualifier,
            )
        else:
            log.warning(
                "trigger_action: HTTP %d from rewards (user=%s… action=%s) — rewards unavailable",
                exc.response.status_code,
                uid,
                action_type,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "trigger_action: network error (user=%s… action=%s): %s",
            uid,
            action_type,
            exc,
        )
    except Exception:
        log.warning(
            "trigger_action: unexpected error (user=%s… action=%s)",
            uid,
            action_type,
            exc_info=True,
        )


def trigger_annual_gift_card(user_id: uuid.UUID, stripe_session_id: str) -> None:
    """
    POST /rewards/gift-cards/annual — fire-and-forget. Never raises.

    Called from ratis_auth after annual checkout.session.completed.
    stripe_session_id is the idempotency key (source_ref_id in gift_card_orders).

    Log levels:
    - ERROR : misconfiguration (invalid URL, HTTP 4xx) — action required.
    - WARNING : transient failure (network, HTTP 5xx) — rewards service may be down.
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    uid = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]

    if not base_url or not key:
        log.warning(
            "trigger_annual_gift_card skipped — REWARDS_BASE_URL or INTERNAL_API_KEY not configured "
            "(user=%s… session=%s)",
            uid,
            stripe_session_id,
        )
        return

    url = f"{base_url}/rewards/gift-cards/annual"
    try:
        resp = httpx.post(
            url,
            json={"user_id": str(user_id), "stripe_session_id": stripe_session_id},
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "trigger_annual_gift_card: invalid REWARDS_BASE_URL %r (user=%s… session=%s) — check config",
            url,
            uid,
            stripe_session_id,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            log.error(
                "trigger_annual_gift_card: HTTP %d (user=%s… session=%s) — check API key",
                exc.response.status_code,
                uid,
                stripe_session_id,
            )
        else:
            log.warning(
                "trigger_annual_gift_card: HTTP %d from rewards (user=%s… session=%s) — rewards unavailable",
                exc.response.status_code,
                uid,
                stripe_session_id,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "trigger_annual_gift_card: network error (user=%s… session=%s): %s",
            uid,
            stripe_session_id,
            exc,
        )
    except Exception:
        log.warning(
            "trigger_annual_gift_card: unexpected error (user=%s… session=%s)",
            uid,
            stripe_session_id,
            exc_info=True,
        )


def trigger_cashback_scan(
    user_id: uuid.UUID,
    receipt_lines: list[dict],
) -> None:
    """
    POST /rewards/cashback/scan-detected — fire-and-forget. Never raises.

    Called once per receipt after commit, with all accepted (matched) lines.
    receipt_lines: list of {"ean": str, "price": float, "scan_id": str} — one
                   entry per accepted scan row. scan_id per line is the idempotency
                   key in cashback_transactions (scan_id, product_ean).

    Log levels:
    - ERROR : misconfiguration (invalid URL, HTTP 4xx) — action required.
    - WARNING : transient failure (network, HTTP 5xx) — rewards service may be down.
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    uid = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]

    if not base_url or not key:
        log.warning(
            "trigger_cashback_scan skipped — REWARDS_BASE_URL or INTERNAL_API_KEY not configured (user=%s… lines=%d)",
            uid,
            len(receipt_lines),
        )
        return

    url = f"{base_url}/rewards/cashback/scan-detected"
    try:
        resp = httpx.post(
            url,
            json={
                "user_id": str(user_id),
                "receipt_lines": receipt_lines,
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "trigger_cashback_scan: invalid REWARDS_BASE_URL %r (user=%s…) — check config",
            url,
            uid,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            log.error(
                "trigger_cashback_scan: HTTP %d (user=%s…) — check API key",
                exc.response.status_code,
                uid,
            )
        else:
            log.warning(
                "trigger_cashback_scan: HTTP %d from rewards (user=%s…) — rewards unavailable",
                exc.response.status_code,
                uid,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "trigger_cashback_scan: network error (user=%s…): %s",
            uid,
            exc,
        )
    except Exception:
        log.warning(
            "trigger_cashback_scan: unexpected error (user=%s…)",
            uid,
            exc_info=True,
        )


def process_retroactive_cashback(store_id: uuid.UUID) -> dict:
    """
    POST /rewards/cashback/process-retroactive — synchronous, raises on failure.

    Called by ratis_batch_consensus Phase 3 (store_validation_phase) after a
    store flips from validation_status='pending' to 'confirmed'. Asks rewards
    to credit cashback for every receipt previously held back on that store.

    Unlike the other helpers in this module, this one **raises** on misconfig
    or HTTP failure — the batch caller wants to count successes vs failures
    in its stats and decide per-store whether to log+continue or abort.

    Returns the rewards JSON payload, e.g.
    ``{"processed_receipts": 3, "total_cashback_cents": 4250}``.
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    if not base_url or not key:
        raise RuntimeError("process_retroactive_cashback: REWARDS_BASE_URL or INTERNAL_API_KEY not configured")

    url = f"{base_url}/rewards/cashback/process-retroactive"
    resp = httpx.post(
        url,
        json={"store_id": str(store_id)},
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def trigger_referral_signup_bonus(referred_user_id: uuid.UUID) -> None:
    """
    POST /rewards/referral/signup-bonus — fire-and-forget. Never raises.

    Called from ratis_auth after ``register()`` creates a referral_uses link
    for a new user who entered a valid referral_code. Credits the new user
    (Y) with +150 CAB flat. Idempotent server-side.
    """
    base_url = (os.environ.get("REWARDS_BASE_URL") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY")
    uid = hashlib.sha256(str(referred_user_id).encode()).hexdigest()[:8]

    if not base_url or not key:
        log.warning(
            "trigger_referral_signup_bonus skipped — REWARDS_BASE_URL or "
            "INTERNAL_API_KEY not configured (referred_user=%s…)",
            uid,
        )
        return

    url = f"{base_url}/rewards/referral/signup-bonus"
    try:
        resp = httpx.post(
            url,
            json={"referred_user_id": str(referred_user_id)},
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "trigger_referral_signup_bonus: invalid REWARDS_BASE_URL %r (user=%s…)",
            url,
            uid,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            log.error(
                "trigger_referral_signup_bonus: HTTP %d (user=%s…) — check API key",
                exc.response.status_code,
                uid,
            )
        else:
            log.warning(
                "trigger_referral_signup_bonus: HTTP %d from rewards (user=%s…) — rewards unavailable",
                exc.response.status_code,
                uid,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "trigger_referral_signup_bonus: network error (user=%s…): %s",
            uid,
            exc,
        )
    except Exception:
        log.warning(
            "trigger_referral_signup_bonus: unexpected error (user=%s…)",
            uid,
            exc_info=True,
        )
