"""One-Time Token (OTT) helpers — Module 10 PR 5.

The OTT flow allows an operator to open an admin UI without ever seeing the
ADMIN_API_KEY in the browser URL or DevTools:

1. ``POST /admin/session-bootstrap`` (ADMIN_API_KEY bearer) → {ott, redirect_url}
2. Browser opens ``{host}/?ott=<jwt>&redirect=<path>``
3. OTT middleware validates + consumes the JWT (Redis SET NX / DELETE ensures
   single-use), sets a session cookie, strips the ?ott= param, and redirects.

Token shape
-----------
Algorithm : HS256 (ADMIN_API_KEY is the shared secret — no separate OTT key).
Claims    : sub="ott", exp=now+ttl, jti=uuid4(), redirect=<path>
Single-use enforcement : Redis ``SET ott:{jti} 1 EX {ttl} NX`` at issuance ;
                         ``DEL ott:{jti}`` (+ missing → 401) at consumption.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from jose import JWTError, jwt


@dataclass(slots=True)
class OTTClaims:
    """Validated OTT claims returned by ``validate_ott_jwt``."""

    sub: str  # always "ott"
    exp: int  # Unix timestamp
    jti: str  # UUID4 string — used as the Redis single-use key
    redirect: str  # the target admin path


def make_ott_jwt(
    admin_key: str,
    *,
    redirect: str,
    ttl_sec: int = 60,
) -> str:
    """Mint a HS256 OTT JWT.

    Args:
        admin_key: The ADMIN_API_KEY (HS256 signing secret).
        redirect:  The target admin path (e.g. "/admin/db-approvals").
        ttl_sec:   Token time-to-live in seconds (default 60).

    Returns:
        Signed JWT string.
    """
    now = int(datetime.now(UTC).timestamp())
    payload: dict[str, object] = {
        "sub": "ott",
        "exp": now + ttl_sec,
        "jti": str(uuid.uuid4()),
        "redirect": redirect,
    }
    return jwt.encode(payload, admin_key, algorithm="HS256")


def validate_ott_jwt(token: str, admin_key: str) -> OTTClaims:
    """Validate a HS256 OTT JWT.

    Checks:
    - Signature (wrong key → ValueError)
    - Expiry (expired → ValueError)
    - ``sub == "ott"``

    Args:
        token:     The raw JWT string.
        admin_key: The ADMIN_API_KEY used to sign the token.

    Returns:
        ``OTTClaims`` on success.

    Raises:
        ValueError("invalid_ott") on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            admin_key,
            algorithms=["HS256"],
            options={"require_exp": True},
        )
    except JWTError:
        raise ValueError("invalid_ott")

    if payload.get("sub") != "ott":
        raise ValueError("invalid_ott")

    jti = payload.get("jti")
    if not jti:
        raise ValueError("invalid_ott")

    redirect = payload.get("redirect")
    if not redirect:
        raise ValueError("invalid_ott")

    return OTTClaims(
        sub=payload["sub"],
        exp=payload["exp"],
        jti=str(jti),
        redirect=str(redirect),
    )
