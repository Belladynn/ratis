"""Local fixtures for admin tests — TOTP env + helpers.

Re-exports the parent conftest fixtures (db, admin_client, raw_client, etc.)
since pytest auto-discovers parent confest. We only add admin-specific
helpers here.
"""

from __future__ import annotations

import os

import pyotp
import pytest

# Same secret across the whole test session — deterministic codes.
_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # pragma: allowlist secret — test fixture only

# Ensure the env var is set BEFORE any TOTP-using import.
os.environ.setdefault("ADMIN_TOTP_SECRET", _TOTP_SECRET)


@pytest.fixture
def totp_secret() -> str:
    """The fixed TOTP secret used by tests (mirrors ADMIN_TOTP_SECRET)."""
    # Re-assert in case another test wiped it (test_no_secret tests do this).
    os.environ["ADMIN_TOTP_SECRET"] = _TOTP_SECRET
    return _TOTP_SECRET


@pytest.fixture
def valid_totp_code(totp_secret: str) -> str:
    """A currently-valid TOTP code for the fixture secret."""
    return pyotp.TOTP(totp_secret).now()


@pytest.fixture
def admin_headers(valid_totp_code: str) -> dict[str, str]:
    """Headers a financial-sensitive admin endpoint expects (TOTP + Operator)."""
    return {
        "X-Admin-TOTP": valid_totp_code,
        "X-Admin-Operator": "test-admin",
    }
