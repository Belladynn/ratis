"""
Inter-service client for ratis_notifier.

Two flavours:

* :func:`notify_user` — legacy minimal API ``(user_id, notif_type, data)``.
  Used by all V0 callers (scan_done, route_ready, retro_cab_gratitude, …).
* :func:`send` — V1.1 extended API for richer push semantics. Adds
  ``visible_push`` (silent vs visible push), ``push_rate_limit_seconds``
  (server-side cooldown, V1.1 enforcement), ``push_title`` / ``push_body``
  (override the default template). Currently used by the achievement
  notification service (rarity-gradated UX).

Usage (fire-and-forget, inside a FastAPI BackgroundTask or Celery task):

    from ratis_core import notifier_client

    notifier_client.notify_user(user.id, "scan_done", {"products": 3})

    notifier_client.send(
        user_id=user.id,
        notif_type="achievement_unlocked",
        payload={"code": "v_first", "rarity": "bronze", ...},
        visible_push=False,
        push_rate_limit_seconds=0,
    )

NOTIFIER_URL and INTERNAL_API_KEY should be configured in the environment.
Missing config is a silent no-op (warning logged) — use require_env() in the
service lifespan to fail fast at startup rather than silently dropping notifications.

NOTIFIER_URL **MUST** be the full ``/notify`` endpoint URL (e.g.
``http://notifier:8005/api/v1/notify``) — both helpers POST directly on
this URL without appending a path. A NOTIFIER_URL without ``/api/v1/notify``
produces 404s that are silently swallowed by fire-and-forget. See
``ARCH_deployment.md § Cross-service URL conventions``.

The call is best-effort: errors are logged and swallowed so that a notification
failure never crashes the calling service.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid

import httpx

log = logging.getLogger("notifier_client")

# Reserved keys that ``send`` injects into the wire ``data`` payload so the
# notifier service can route push behaviour (visible vs silent, cooldown,
# template override). Kept here as the single source of truth for downstream
# parsers in ratis_notifier.
_RESERVED_PAYLOAD_KEYS = (
    "_visible_push",
    "_push_rate_limit_seconds",
    "_push_title",
    "_push_body",
)


def notify_user(
    user_id: uuid.UUID,
    notif_type: str,
    data: dict | None = None,
) -> None:
    """
    POST to ratis_notifier — fire-and-forget. Never raises.

    Log levels:
    - ERROR : misconfiguration (invalid URL, HTTP 4xx) — action required.
    - WARNING : transient failure (network, HTTP 5xx) — notifier may be down.
    """
    url = os.environ.get("NOTIFIER_URL", "")
    key = os.environ.get("INTERNAL_API_KEY", "")
    uid = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]  # one-way hash, never raw UUID

    if not url or not key:
        log.warning(
            "notify_user skipped — NOTIFIER_URL or INTERNAL_API_KEY not configured (user=%.8s… type=%s)",
            user_id,
            notif_type,
        )
        return

    try:
        resp = httpx.post(
            url,
            json={"user_id": str(user_id), "type": notif_type, "data": data or {}},
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "notify_user: invalid NOTIFIER_URL %r (user=%s… type=%s) — check config",
            url,
            uid,
            notif_type,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            # 4xx = caller misconfiguration: wrong key, malformed payload
            log.error(
                "notify_user: HTTP %d from notifier (user=%s… type=%s) — check INTERNAL_API_KEY and payload",
                exc.response.status_code,
                uid,
                notif_type,
            )
        else:
            # 5xx = notifier is down or crashing — transient
            log.warning(
                "notify_user: HTTP %d from notifier (user=%s… type=%s) — notifier unavailable",
                exc.response.status_code,
                uid,
                notif_type,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "notify_user: network error (user=%s… type=%s): %s",
            uid,
            notif_type,
            exc,
        )
    except Exception:
        log.warning(
            "notify_user: unexpected error (user=%s… type=%s)",
            uid,
            notif_type,
            exc_info=True,
        )


def send(
    *,
    user_id: uuid.UUID,
    notif_type: str,
    payload: dict | None = None,
    visible_push: bool = True,
    push_rate_limit_seconds: int = 0,
    push_title: str | None = None,
    push_body: str | None = None,
) -> None:
    """Extended client — fire-and-forget. Never raises.

    Adds the following knobs on top of :func:`notify_user`:

    * ``visible_push`` (default True) — when False, the notifier MAY suppress
      the OS-level push (still record/route the in-app payload). Used by
      low-rarity achievements (terracotta / bronze / copper / silver / gold /
      emerald) where the toast + optional modal are enough.
    * ``push_rate_limit_seconds`` (default 0) — server-side cooldown the
      notifier should enforce between two visible pushes for this user. 0 = no
      cooldown. Sapphire/Ruby achievements pass 3600 (1h). Crystal/Diamant
      pass 0 (extreme rarity, no rate-limit). The actual enforcement lives in
      ratis_notifier — see V1.1 follow-up.
    * ``push_title`` / ``push_body`` — explicit override of the
      ``ratis_settings.notifier.notification_types[<type>]`` template. Useful
      when the title/body is dynamic per-row (e.g. achievement label).

    All extra params are bundled into the wire ``data`` field with reserved
    underscore-prefixed keys (``_visible_push``, ``_push_rate_limit_seconds``,
    ``_push_title``, ``_push_body``) so the notifier service can interpret
    them without changing the inter-service contract surface (one POST
    endpoint, one schema). Caller-supplied ``payload`` keys with the same
    underscore prefix are silently overwritten — these are reserved.
    """
    url = os.environ.get("NOTIFIER_URL", "")
    key = os.environ.get("INTERNAL_API_KEY", "")
    uid = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]

    if not url or not key:
        log.warning(
            "notifier_client.send skipped — NOTIFIER_URL or INTERNAL_API_KEY not configured (user=%s… type=%s)",
            uid,
            notif_type,
        )
        return

    data: dict = dict(payload or {})
    # Strip then re-inject reserved keys — caller cannot poison them.
    for k in _RESERVED_PAYLOAD_KEYS:
        data.pop(k, None)
    data["_visible_push"] = bool(visible_push)
    data["_push_rate_limit_seconds"] = int(push_rate_limit_seconds)
    if push_title is not None:
        data["_push_title"] = str(push_title)
    if push_body is not None:
        data["_push_body"] = str(push_body)

    try:
        resp = httpx.post(
            url,
            json={"user_id": str(user_id), "type": notif_type, "data": data},
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.InvalidURL:
        log.error(
            "notifier_client.send: invalid NOTIFIER_URL %r (user=%s… type=%s) — check config",
            url,
            uid,
            notif_type,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            log.error(
                "notifier_client.send: HTTP %d from notifier (user=%s… type=%s) — check INTERNAL_API_KEY and payload",
                exc.response.status_code,
                uid,
                notif_type,
            )
        else:
            log.warning(
                "notifier_client.send: HTTP %d from notifier (user=%s… type=%s) — notifier unavailable",
                exc.response.status_code,
                uid,
                notif_type,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning(
            "notifier_client.send: network error (user=%s… type=%s): %s",
            uid,
            notif_type,
            exc,
        )
    except Exception:
        log.warning(
            "notifier_client.send: unexpected error (user=%s… type=%s)",
            uid,
            notif_type,
            exc_info=True,
        )
