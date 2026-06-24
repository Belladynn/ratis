"""slowapi rate-limiter for the ratis_rewards service.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § 7
"Endpoints API" — POST /rewards/achievements/secret-event uses a per-user
rate-limit (10/h/user) to prevent farming the secret events catalog.

The default key is the JWT subject (user UUID) ; falls back to the
remote IP if no JWT is present (which then 401's on the auth dep, but
the limiter sits BEFORE auth in slowapi's dispatch — never raise on
key extraction).

Pattern mirrors ``webservices/ratis_auth/limiter.py`` (slowapi 0.1.x).
"""

from __future__ import annotations

from fastapi import Request
from ratis_core.jwt import decode_access_token
from slowapi import Limiter
from slowapi.util import get_remote_address


def _jwt_user_or_ip(request: Request) -> str:
    """Identify the caller — JWT subject (preferred) or IP fallback.

    Authorization header may be absent (the route still 401's via its
    own ``Depends(get_current_user)``) — when that happens we fall back
    to the remote address so anonymous floods can't bypass the limit by
    omitting the header. ``decode_access_token`` failures (malformed
    token, wrong signature) also fall back to IP — never raise here, the
    limiter must be transparent.
    """
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            user_id, _ = decode_access_token(token)
        except Exception:
            return get_remote_address(request)
        return f"user:{user_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=_jwt_user_or_ip)
