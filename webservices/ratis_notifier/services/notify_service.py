"""
Notification pipeline.

Fire-and-forget: called from a FastAPI BackgroundTask. Never raises — all
errors are logged to notification_logs and swallowed.

Pipeline:
  1. Quiet hours check → log skipped
  2. Daily cap check   → log skipped
  3. Visible-push gate (V1.1) — caller-supplied ``_visible_push`` (default
     True) decides whether the OS push fires at all. When False the call is
     a silent / data-only push : no Expo POST, log status="skipped"
     reason="data_only_push". The FE in-app notification path (achievement
     bus) still surfaces the unlock independently.
  4. Push rate-limit (V1.1) — when ``_push_rate_limit_seconds > 0`` we ask
     the rate-limiter (Redis SETNX cooldown) ; if denied we DOWNGRADE the
     push to data-only (same skip path as step 3). Decision rationale:
     the user still sees the unlock the next time they open the app
     (toast/modal/badge driven by FE bus); we just don't double-tap them
     with an OS notification banner.
  5. Fetch push tokens → silent return if none.
  6. For each token: send via Expo (reserved underscore-prefixed keys
     stripped from the data payload before send so they don't leak).
     - DeviceNotRegistered → delete token
     - HTTP / other error  → retry with exponential backoff
     - Success             → collect ticket_id
  7. One log per call (sent/failed) — IntegrityError on concurrent duplicate
     → silent dedup.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import repositories.notification_repository as notif_repo
from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from services.push_rate_limiter import PushRateLimiter

log = logging.getLogger("notifier")


# Reserved keys injected by ``ratis_core.notifier_client.send`` — kept in
# sync with that module's ``_RESERVED_PAYLOAD_KEYS``. Stripped before the
# Expo POST so internal routing flags don't leak to the device.
_RESERVED_PAYLOAD_KEYS = (
    "_visible_push",
    "_push_rate_limit_seconds",
    "_push_title",
    "_push_body",
)


# ── Template interpolation ────────────────────────────────────────────────────


class _SafeDict(dict):
    """dict that returns "{key}" for missing keys so templates don't crash."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render(template: str, data: dict) -> str:
    """Interpolate {key} placeholders from `data` into `template`.

    Missing keys are left as-is. Malformed templates fall back to the raw
    string — rendering must never break the pipeline.
    """
    try:
        return template.format_map(_SafeDict(data))
    except (IndexError, ValueError):
        return template


# ── FastAPI dependencies — injectable in tests ────────────────────────────────


def get_now():
    """Dependency: returns a callable that produces the current UTC datetime."""
    return lambda: datetime.now(UTC)


def get_rate_limiter(request: Request) -> PushRateLimiter:
    """Dependency: returns the app-wide PushRateLimiter (Redis-backed in
    prod, fakeredis-backed in tests via dependency override)."""
    return request.app.state.rate_limiter


# ── Expo helpers ───────────────────────────────────────────────────────────────


class DeviceNotRegisteredError(Exception):
    pass


class ExpoAPIError(Exception):
    pass


def _send_expo(
    token_str: str,
    title: str,
    body: str,
    data: dict,
    expo_url: str,
    attempts: int,
    delay_seconds: int,
) -> str | None:
    """
    Send a single push notification via Expo Push API.

    Returns the expo ticket_id on success.
    Raises DeviceNotRegisteredError if the token is no longer valid.
    Raises ExpoAPIError on non-retryable Expo errors.
    Raises httpx.HTTPError on network failure after all retries.
    """

    def _do() -> str | None:
        response = httpx.post(
            expo_url,
            json={"to": token_str, "title": title, "body": body, "data": data},
            timeout=10,
        )
        response.raise_for_status()
        try:
            result = response.json()["data"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise ExpoAPIError(f"unexpected Expo response: {exc}") from exc
        if result.get("status") == "error":
            error_code = result.get("details", {}).get("error", "")
            if error_code == "DeviceNotRegistered":
                raise DeviceNotRegisteredError(token_str)
            raise ExpoAPIError(result.get("message", "expo_error"))
        return result.get("id")

    retrying = Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=delay_seconds, max=delay_seconds * 4),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    return retrying(_do)


# ── Anti-spam helpers ──────────────────────────────────────────────────────────


def _to_local(now_utc: datetime, user_tz: str) -> datetime:
    """Convert a UTC datetime to the user's local time. Falls back to UTC on invalid timezone.
    If now_utc is naive it is assumed to be UTC."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    try:
        return now_utc.astimezone(ZoneInfo(user_tz))
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        # ValueError covers ZoneInfo('') — empty / malformed timezone string.
        return now_utc


def _is_quiet_hours(now_utc: datetime, cfg: dict, user_tz: str) -> bool:
    """Check quiet hours against the user's local time."""
    h = _to_local(now_utc, user_tz).hour
    start: int = cfg["quiet_hours_start"]
    end: int = cfg["quiet_hours_end"]
    # Wraps midnight: e.g. start=22, end=8 → [22..23] ∪ [0..7]
    if start > end:
        return h >= start or h < end
    return start <= h < end


def _cap_since(now_utc: datetime, user_tz: str) -> datetime:
    """Return the UTC datetime of local midnight — start of 'today' for this user."""
    local_midnight = _to_local(now_utc, user_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC)


# ── Main pipeline ──────────────────────────────────────────────────────────────


def send_notification(
    db: Session,
    user_id: uuid.UUID,
    notif_type: str,
    data: dict,
    cfg: dict,
    now: datetime,
    rate_limiter: PushRateLimiter | None = None,
) -> None:
    """
    Full notification pipeline. Never raises — errors are logged and swallowed.
    Intended to run as a FastAPI BackgroundTask.

    ``rate_limiter`` is optional for backward compat with legacy unit tests
    that import this function directly ; production callers always pass one
    via the route dependency.
    """
    try:
        _run_pipeline(db, user_id, notif_type, data, cfg, now, rate_limiter)
    except Exception:
        db.rollback()
        log.exception(
            "Unexpected error in notification pipeline for user %s type=%s",
            user_id,
            notif_type,
        )


def _skip(
    db: Session,
    user_id: uuid.UUID,
    notif_type: str,
    now: datetime,
    reason: str,
) -> None:
    log.info("Notification skipped (%s) for user %s type=%s", reason, user_id, notif_type)
    notif_repo.create_log(db, user_id, notif_type, "skipped", None, None, now)
    db.commit()


def _extract_routing_flags(data: dict) -> tuple[bool, int, str | None, str | None, dict]:
    """Pop the V1.1 reserved keys out of the wire payload.

    Returns ``(visible_push, push_rate_limit_seconds, push_title_override,
    push_body_override, clean_data)``.

    Defaults preserve V0 behaviour for callers that don't set the keys
    (every legacy notif_type — scan_done, etc.) : visible push, no rate
    limit, no title/body override.

    The returned ``clean_data`` is a shallow copy of the input with the
    reserved keys removed — it's what goes on the wire to Expo so internal
    routing flags never leak to the device.
    """
    clean = dict(data or {})
    visible_push = bool(clean.pop("_visible_push", True))
    rate_limit_seconds = int(clean.pop("_push_rate_limit_seconds", 0) or 0)
    title_override: Any = clean.pop("_push_title", None)
    body_override: Any = clean.pop("_push_body", None)
    return (
        visible_push,
        rate_limit_seconds,
        str(title_override) if title_override is not None else None,
        str(body_override) if body_override is not None else None,
        clean,
    )


def _run_pipeline(
    db: Session,
    user_id: uuid.UUID,
    notif_type: str,
    data: dict,
    cfg: dict,
    now: datetime,
    rate_limiter: PushRateLimiter | None,
) -> None:
    notifier_cfg = cfg["notifier"]
    # EXPO_PUSH_URL env var wins over the JSON settings value when set —
    # keeps the deploy-time override (.env / docker-compose) authoritative.
    expo_url: str = os.environ.get("EXPO_PUSH_URL") or notifier_cfg["expo_push_url"]

    (
        visible_push,
        rate_limit_seconds,
        title_override,
        body_override,
        clean_data,
    ) = _extract_routing_flags(data)

    # 1. Quiet hours — evaluated in the user's local timezone
    user_tz = notif_repo.get_user_timezone(db, user_id)
    if _is_quiet_hours(now, notifier_cfg, user_tz):
        _skip(db, user_id, notif_type, now, "quiet hours")
        return

    # 2. Daily cap — reset at local midnight.
    #    Per-user advisory lock serialises the count+insert : without it two
    #    concurrent notify requests for the same user both read the count
    #    below the cap and both proceed, exceeding the cap. The lock is
    #    transaction-scoped — released when this pipeline's transaction
    #    commits (the _skip / send-path commits below) or rolls back.
    notif_repo.acquire_user_cap_lock(db, user_id)
    sent_today = notif_repo.count_sent_today(db, user_id, _cap_since(now, user_tz))
    if sent_today >= notifier_cfg["max_notifications_per_day"]:
        _skip(db, user_id, notif_type, now, "daily limit")
        return

    # 3. Visible-push gate (V1.1) — caller asked for a silent / data-only
    # push. The FE in-app notification path (achievement bus) will surface
    # the unlock when the app comes to the foreground.
    if not visible_push:
        _skip(db, user_id, notif_type, now, "data_only_push")
        return

    # 4. Push rate-limit (V1.1) — Saphir/Rubis pass 3600s. If the user
    # already received a visible push for this notif_type within the
    # cooldown window, downgrade to silent (same skip path).
    if (
        rate_limit_seconds > 0
        and rate_limiter is not None
        and not rate_limiter.allow_push(user_id, notif_type, rate_limit_seconds)
    ):
        _skip(db, user_id, notif_type, now, "push_rate_limited")
        return

    # 5. Tokens
    tokens = notif_repo.get_tokens(db, user_id)
    if not tokens:
        log.debug("No push tokens for user %s — silently ignored", user_id)
        db.commit()
        return

    # 6. Send to all tokens — collect outcome
    types_cfg = notifier_cfg.get("notification_types", {})
    type_info = types_cfg.get(notif_type, {})
    template_title = type_info.get("title", notif_type)
    template_body = type_info.get("body", "")
    # Caller-supplied title/body override the settings template (V1.1 — used
    # by achievements where the title is dynamic per-row "🏆 Trophée Saphir
    # !"). Override values still pass through ``_render`` so {placeholders}
    # in them are interpolated against the clean data.
    title: str = _render(title_override or template_title, clean_data)
    body: str = _render(body_override or template_body, clean_data)

    first_ticket_id: str | None = None
    any_sent = False
    for token in tokens:
        try:
            ticket_id = _send_expo(
                token.token,
                title,
                body,
                clean_data,
                expo_url,
                attempts=notifier_cfg["retry_attempts"],
                delay_seconds=notifier_cfg["retry_delay_seconds"],
            )
            if first_ticket_id is None:
                first_ticket_id = ticket_id
            any_sent = True
            # Persist the ticket so ``ratis_batch_push_receipts`` can poll
            # Expo's receipts endpoint and clean up dead tokens. One row per
            # (send, token) — the receipt's outcome maps back to this token.
            if ticket_id:
                notif_repo.create_receipt_ticket(db, user_id, token.token, ticket_id)
            log.info(
                "Notification sent to token %s user=%s type=%s ticket=%s",
                token.id,
                user_id,
                notif_type,
                ticket_id,
            )
        except DeviceNotRegisteredError:
            log.info("Token %s invalid (DeviceNotRegistered) — deleting", token.id)
            notif_repo.delete_token(db, token.id)
        except Exception:
            log.exception("Failed to send to token %s (user %s)", token.id, user_id)

    # 7. One log per pipeline call.
    #    INSERT directly — unique index on (user_id, type, date_trunc('minute', sent_at))
    #    catches concurrent duplicate requests atomically.
    status = "sent" if any_sent else "failed"
    try:
        notif_repo.create_log(db, user_id, notif_type, status, clean_data, first_ticket_id, now)
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info(
            "Notification deduped (concurrent request) for user %s type=%s",
            user_id,
            notif_type,
        )
