"""TOTP-based 2FA — thin re-export wrapper around ``ratis_core.totp_service``.

Historical home of ``verify_totp_dep`` ; the canonical implementation now
lives in ``ratis_core.totp_service`` so AU, RW (and any future admin-bearing
service) share the same ``ADMIN_TOTP_SECRET`` contract — single source of
truth (R33). This module is kept for backwards compatibility of in-repo
imports (``from services.totp_service import verify_totp_dep``).

Generation script : ``python -m tools.setup_totp`` prints a provisioning
URI to scan with Google Authenticator (one-shot, secret then in env).
"""

from __future__ import annotations

from ratis_core.totp_service import verify_totp_dep

__all__ = ["verify_totp_dep"]
