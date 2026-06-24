"""OTT session-bootstrap endpoint — Module 10, PR 5.

``POST /admin/session-bootstrap`` issues a single-use JWT (OTT) that the
``ratis-admin open`` CLI passes to the browser. The browser opens

    https://{host}/?ott=<jwt>&redirect=<path>

A middleware on each service consumes the OTT (Redis SET NX / DEL for
single-use enforcement), sets a session cookie, and redirects. The
operator never sees the ADMIN_API_KEY in the browser URL bar.

Auth pattern : ``ADMIN_API_KEY`` Bearer (``verify_admin_key`` dep).
No TOTP — the token is 60 s single-use; the real session is established
by the mini-UI login cookie after consumption.

Redis single-use enforcement (NX flag):
- At issuance  : ``SET ott:{jti} 1 EX 60 NX`` — if NX fails (key exists)
  the token was already issued with the same jti (uuid4 collision, ~0).
- At consumption (middleware): ``DEL ott:{jti}``; returns 0 → already used.
"""

from __future__ import annotations

import os

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from ratis_core.deps import verify_admin_key
from ratis_core.ott import make_ott_jwt

router = APIRouter()

# ---------------------------------------------------------------------------
# Default redirect path — safe fallback when the CLI does not specify one.
# ---------------------------------------------------------------------------
_DEFAULT_REDIRECT = "/admin/ui"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SessionBootstrapRequest(BaseModel):
    """Optional redirect path for the OTT.

    If omitted the token redirects to the service's default admin UI root.
    """

    redirect: str = _DEFAULT_REDIRECT


class SessionBootstrapResponse(BaseModel):
    ott: str
    redirect_url: str


# ---------------------------------------------------------------------------
# Redis dependency — injectable for tests
# ---------------------------------------------------------------------------


def get_redis() -> redis_lib.Redis:
    """Return a Redis client from REDIS_URL. Raises 503 if unavailable."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ott_redis_unavailable",
        )
    return redis_lib.from_url(url, decode_responses=True)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/admin/session-bootstrap",
    response_model=SessionBootstrapResponse,
    summary="Issue a single-use OTT for admin UI access (ratis-admin open).",
)
def session_bootstrap(
    request: Request,
    payload: SessionBootstrapRequest = SessionBootstrapRequest(),
    _: None = Depends(verify_admin_key),
    redis: redis_lib.Redis = Depends(get_redis),
) -> SessionBootstrapResponse:
    """Mint a 60-second single-use OTT and return the browser-ready URL.

    The token is signed with ADMIN_API_KEY (HS256). The ``jti`` is stored
    in Redis (``SET ott:{jti} 1 EX 60 NX``) to enforce single-use.
    """
    admin_key = os.environ.get("ADMIN_API_KEY", "")
    if not admin_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin_api_key_not_configured",
        )

    ott = make_ott_jwt(admin_key, redirect=payload.redirect)

    # Decode the jti from the token to register it in Redis.
    # We use python-jose to peek without verification (we just signed it).
    from jose import jwt as _jwt

    claims = _jwt.get_unverified_claims(ott)
    jti = claims["jti"]
    ttl = 60  # seconds — matches make_ott_jwt default

    # NX = only set if not exists. Collision probability with uuid4 is ~0.
    redis.set(f"ott:{jti}", "1", ex=ttl, nx=True)

    # Build the redirect URL from the request's base URL.
    base_url = str(request.base_url).rstrip("/")
    redirect_url = f"{base_url}/?ott={ott}&redirect={payload.redirect}"

    return SessionBootstrapResponse(ott=ott, redirect_url=redirect_url)
