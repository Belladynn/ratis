"""Cookie-based session for the admin mini UI.

The ``/admin/ui/*`` UI cannot easily set ``Authorization: Bearer ...``
headers from a browser form, so we trade the JSON API's bearer for a
HTTP-only / SameSite=Strict cookie. The cookie carries a deterministic
HMAC-SHA256 token keyed by ``ADMIN_API_KEY`` over the operator handle
— recomputable on every request from the current env var. Rotating the
key invalidates every existing cookie immediately, no DB / Redis
session store required.

The token uses HMAC (not raw SHA256) to defend against rainbow tables
on weak / short keys and to use the canonical KDF semantics for
keyed-message authentication. The boot-time check
``require_env_min_length("ADMIN_API_KEY", 32)`` enforces a minimum key
length so the HMAC has a meaningful security margin even if an op
copy-pastes a short key into the env (M1 audit sécurité 2026-05-03).

Comparison is constant-time via :func:`hmac.compare_digest` to avoid
timing attacks against the token (and indirectly against the key).

Also exposes :func:`get_admin_session` — a FastAPI dependency that
either returns the bound operator handle or 302-redirects the caller
to ``/admin/ui/login``. Routes that mutate state read the operator
out of this dep and forward it as ``X-Admin-Operator`` into the
service-layer audit calls — the same handle that would have been sent
in the bearer-flavoured admin JSON API.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

COOKIE_NAME = "admin_session"
# Self-declared operator handle is stored alongside the token in a
# second cookie ; the token alone cannot be reversed back into the
# handle. Both must round-trip per request.
OPERATOR_COOKIE_NAME = "admin_operator"

# Cookie max-age — 12h is long enough for a working session, short
# enough that a forgotten machine doesn't stay logged in indefinitely.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


def compute_token(api_key: str, operator: str) -> str:
    """Return the deterministic HMAC-SHA256 session token for ``(api_key, operator)``.

    Stable across processes (no salt) — the goal is a
    server-recomputable opaque value, not a credential database. A
    leaked token still requires the matching ``operator`` to be
    accepted, and a key rotation invalidates everything in one step.

    M1 (audit sécurité 2026-05-03) — keyed HMAC instead of raw SHA256
    over a concatenation : (a) canonical KDF semantics for
    keyed-message authentication, (b) defense against rainbow tables
    on weak / short keys (paired with the
    ``require_env_min_length("ADMIN_API_KEY", 32)`` boot guard).
    """
    return hmac.new(
        key=api_key.encode("utf-8"),
        msg=operator.encode("utf-8"),
        digestmod="sha256",
    ).hexdigest()


def verify_credentials(api_key: str) -> bool:
    """Constant-time check of the submitted ``api_key`` against the env.

    Returns False when ``ADMIN_API_KEY`` is unset to keep the route
    side of the world honest — a misconfigured prod that lost the env
    var must not silently accept any key. The mount-time guard in
    ``main.py`` already prevents the router from binding when the env
    var is absent ; this is belt-and-braces.
    """
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not expected or not api_key:
        return False
    return hmac.compare_digest(api_key, expected)


def verify_session_cookie(token: str | None, operator: str | None) -> str | None:
    """Validate a cookie pair and return the bound operator on success.

    ``None`` on any failure (missing token, missing operator, mismatch,
    or env var rotated). The route layer maps ``None`` to a redirect.
    """
    if not token or not operator:
        return None
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not expected:
        return None
    expected_token = compute_token(expected, operator)
    if hmac.compare_digest(token, expected_token):
        return operator
    return None


@dataclass(frozen=True)
class AdminSession:
    """Bound operator handle for one in-flight ``/admin/ui/*`` request."""

    operator: str


def get_admin_session(request: Request) -> AdminSession:
    """FastAPI dep — extract + verify the cookie, redirect on failure.

    On a missing / invalid cookie we raise an ``HTTPException`` whose
    handler in ``routes`` swaps it for a 302 to ``/admin/ui/login``.
    Pure FastAPI dep usage : no globals, no overrides at test time —
    each test sets the cookie via the TestClient session.
    """
    token = request.cookies.get(COOKIE_NAME)
    operator = request.cookies.get(OPERATOR_COOKIE_NAME)
    handle = verify_session_cookie(token, operator)
    if handle is None:
        # Custom status code carrier — see ``_login_redirect_handler``
        # in ``routes`` for the actual 302 response shape.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login_required")
    return AdminSession(operator=handle)


def build_login_redirect() -> RedirectResponse:
    """Common 302 used by both the dep handler and explicit logout."""
    return RedirectResponse(url="/admin/ui/login", status_code=status.HTTP_302_FOUND)
