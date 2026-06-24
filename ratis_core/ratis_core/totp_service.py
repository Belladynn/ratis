"""TOTP-based 2FA for financial-sensitive admin endpoints (shared).

Pattern : ``ADMIN_API_KEY`` (always, via ``ratis_core.deps.verify_admin_key``)
+ ``X-Admin-TOTP`` header (only on routes flagged ``requires_totp=True``).

Single TOTP secret env var (``ADMIN_TOTP_SECRET``) — shared across services
that expose ``/admin/*`` mutation endpoints (RW, AU, ...). One secret, one
admin device, deterministic codes everywhere.

Migration to per-admin secrets is documented in ``DECISIONS_PENDING.md``
(post-bêta, ≥3 ops).

Why this lives in ratis_core (not in each service)
---------------------------------------------------
The same ``ADMIN_TOTP_SECRET`` env var is consumed by every service that
exposes admin mutations. Duplicating ``verify_totp_dep`` across services
would mean rotating the valid-window contract independently — a foot-gun.
Consolidating here ensures a single source of truth (R33).

Generation script (one-shot) : ``python -m tools.setup_totp`` prints a
provisioning URI to scan with Google Authenticator ; the admin then sets
``ADMIN_TOTP_SECRET=<base32>`` in every relevant service's env.

Why TOTP instead of WebAuthn / per-key 2FA :
- Stateless — no DB table to migrate, no key rotation flow to maintain.
- Compatible with Google Authenticator (no proprietary client).
- Sufficient defence-in-depth on top of ``ADMIN_API_KEY`` for the alpha.
"""

from __future__ import annotations

import os

import pyotp
from fastapi import Header, HTTPException, status


def verify_totp_dep(
    x_admin_totp: str | None = Header(default=None, alias="X-Admin-TOTP"),
) -> None:
    """FastAPI dependency — raises 401 if TOTP missing or invalid.

    Time window 30 s ± 1 (accepts current + previous code) to handle minor
    clock skew between admin device and server. ``valid_window=1`` is the
    pyotp idiom — wider windows weaken security.

    Errors :
    - 500 ``admin_totp_not_configured`` if the env var is unset (operator
      error, fail-fast — no silent bypass).
    - 401 ``totp_required`` if header missing.
    - 401 ``totp_invalid`` if header present but code wrong / expired.
    """
    secret = os.environ.get("ADMIN_TOTP_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="admin_totp_not_configured",
        )
    if not x_admin_totp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="totp_required",
        )
    totp = pyotp.TOTP(secret)
    if not totp.verify(x_admin_totp, valid_window=1):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="totp_invalid",
        )
