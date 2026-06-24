"""Push rate-limiter — Redis SETNX cooldown for visible OS pushes.

Achievements V1.1 wired the ``push_rate_limit_seconds`` knob in
``ratis_core.notifier_client.send`` (Saphir / Rubis: 3600s, Crystal /
Diamant: 0s). Until V1.1 the value travelled to ``data._push_rate_limit_seconds``
but was never read — the notifier always fired the visible push.

This module closes the gap. The contract :

* Key shape :  ``notif:push:rate:<user_id>:<notif_type>``
* TTL       :  the seconds value carried by the caller (0 → no rate-limit
               check at all, fall through to send).
* Atomicity :  ``SET ... NX EX <ttl>`` — single Redis round-trip, race-free.
* Outcome   :  ``True`` (push allowed) when SETNX wrote the key, ``False``
               (push must be skipped) when the key already existed.

Fail-open contract : ANY Redis failure (connection refused, timeout, etc.)
returns ``True`` — we'd rather over-deliver one push than silence a critical
notification because Redis blinked. Errors are logged so we can trace fail-
opens in production.

Why fail-OPEN, not fail-closed ?
  - Push notifications are user-visible UX. A false negative ("achievement
    silently dropped") is a cold confusion for the user.
  - Rate-limiting is a polish feature, not a security boundary.
  - Redis outage is a transient infra blip — survival > strictness.

Wired into the lifespan via ``app.state.rate_limiter``. Tests inject a
fakeredis-backed instance through the FastAPI dependency-override seam
(``get_rate_limiter`` in ``services.notify_service``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

import redis

logger = logging.getLogger(__name__)


# Single source of truth for the key namespace — easier to refactor + grep.
_KEY_PREFIX = "notif:push:rate"


def _key(user_id: uuid.UUID, notif_type: str) -> str:
    return f"{_KEY_PREFIX}:{user_id}:{notif_type}"


class PushRateLimiter(Protocol):
    """Protocol so tests can hand-roll a stub without depending on redis-py."""

    def allow_push(
        self,
        user_id: uuid.UUID,
        notif_type: str,
        cooldown_seconds: int,
    ) -> bool:
        """Return True when the push may proceed, False when rate-limited."""
        ...


class RedisPushRateLimiter:
    """Default Redis-backed implementation. Constructed from a redis client."""

    def __init__(self, client: redis.Redis):
        self._client = client

    def allow_push(
        self,
        user_id: uuid.UUID,
        notif_type: str,
        cooldown_seconds: int,
    ) -> bool:
        # cooldown_seconds <= 0 means "no rate-limit configured for this
        # rarity" → always allow (preserves the 0-default for non-V1.1
        # callers, e.g. scan_done).
        if cooldown_seconds <= 0:
            return True

        key = _key(user_id, notif_type)
        try:
            # SET NX EX = atomic "set if not exists with TTL". Returns True
            # when the key was written, None / False when it already existed.
            wrote = self._client.set(key, b"1", ex=cooldown_seconds, nx=True)
            return bool(wrote)
        except redis.RedisError:
            logger.exception(
                "push_rate_limit_redis_error — failing OPEN (push will fire) user=%s type=%s cooldown=%ds",
                user_id,
                notif_type,
                cooldown_seconds,
            )
            return True


def make_redis_rate_limiter(redis_url: str) -> RedisPushRateLimiter:
    """Build a real Redis-backed limiter from a connection URL."""
    client = redis.Redis.from_url(redis_url)
    return RedisPushRateLimiter(client)
