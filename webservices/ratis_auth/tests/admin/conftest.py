"""Local fixtures for AU admin tests — TOTP env + admin_client + helpers.

Re-uses parent conftest fixtures (db, raw_client, setup_db) ; this layer
adds the admin-specific ones :

- ``ADMIN_API_KEY`` and ``ADMIN_TOTP_SECRET`` env vars (set BEFORE any
  TOTP-using import is evaluated).
- ``admin_client`` : TestClient with DB override AND admin key auth bypassed
  (matches the RW pattern). Tests that exercise auth itself use raw_client.
- ``admin_headers`` : valid TOTP + Operator headers for mutation tests.
- ``make_user`` / ``make_subscription`` helpers — minimal seed factories
  that bypass the auth_service / Stripe paths so admin tests stay focused
  on the admin-side logic.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

# Same secret across the whole test session — deterministic codes.
_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # pragma: allowlist secret — test fixture only
_ADMIN_KEY = "test-admin-key-padded-to-32-chars-min"  # pragma: allowlist secret — test fixture only

# Ensure env is set BEFORE any TOTP-using import. The parent conftest sets
# JWT_SECRET / DATABASE_URL etc. ; we only add the admin-specific knobs.
os.environ.setdefault("ADMIN_API_KEY", _ADMIN_KEY)
os.environ.setdefault("ADMIN_TOTP_SECRET", _TOTP_SECRET)

import pyotp
import pytest
from fastapi.testclient import TestClient
from main import app
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.identifiers import generate_support_id
from sqlalchemy import text
from sqlalchemy.orm import Session


@pytest.fixture
def totp_secret() -> str:
    """Fixed TOTP secret used by tests (mirrors ADMIN_TOTP_SECRET)."""
    # Re-assert in case a test wiped it (tests that exercise the no-secret
    # 500 path delete the env var via monkeypatch).
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


@pytest.fixture
def bypass_admin_auth():
    """Bypass admin key auth for tests that don't exercise the auth gate itself."""
    app.dependency_overrides[verify_admin_key] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


@pytest.fixture
def admin_client(db, bypass_admin_auth):
    """TestClient with DB override and admin key auth bypassed."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def make_user(db: Session, *, email: str | None = None) -> uuid.UUID:
    """Insert a minimal user row directly. No auth_service / OAuth involved."""
    user_id = uuid.uuid4()
    email = email or f"admin_sub_user_{user_id.hex[:8]}@test.com"
    db.execute(
        text(
            "INSERT INTO users (id, email, support_id, account_type, "
            "                  created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', now(), now())"
        ),
        {
            "id": user_id,
            "email": email,
            "sid": generate_support_id(),
        },
    )
    db.commit()
    return user_id


def make_subscription(
    db: Session,
    user_id: uuid.UUID,
    *,
    status: str = "active",
    plan: str | None = "monthly",
    expires_in_days: int = 30,
    payment_ref: str | None = "test_ref_admin",
) -> uuid.UUID:
    """Insert a subscription row directly. ``payment_ref`` defaults to a stub
    string so the ``payment_ref_coherence`` CHECK constraint stays satisfied
    on ``status='active'``.

    For ``status='pending'`` callers can pass ``payment_ref=None`` — the CHECK
    only fires when status is in ('active', 'expired').
    """
    sub_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=expires_in_days)
    db.execute(
        text(
            "INSERT INTO subscriptions "
            "    (id, user_id, status, plan, price, paid_with, payment_ref, "
            "     started_at, expires_at, cancelled_at) "
            "VALUES (:id, :uid, :status, :plan, 11.99, 'stripe', :payref, "
            "        :started, :expires, NULL)"
        ),
        {
            "id": sub_id,
            "uid": user_id,
            "status": status,
            "plan": plan,
            "payref": payment_ref,
            "started": now,
            "expires": expires_at,
        },
    )
    db.commit()
    return sub_id
