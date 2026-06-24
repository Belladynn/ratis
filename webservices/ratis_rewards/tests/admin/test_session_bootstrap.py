"""TDD coverage for POST /api/v1/admin/session-bootstrap (RW service, Module 10 PR 5).

Tests:
- Correct ADMIN_API_KEY → 200 + {ott, redirect_url}
- Wrong key → 403 (matches verify_admin_key convention)
- No key → 403
- OTT is a valid HS256 JWT with expected claims
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from main import app
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key

_ADMIN_KEY = "test-admin-key-padded-to-32-chars-min"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """Fake Redis that always succeeds for SET NX."""
    redis = MagicMock()
    redis.set.return_value = True
    redis.delete.return_value = 1
    return redis


@pytest.fixture
def client_with_mock_redis(db, mock_redis):
    """TestClient with DB override + admin auth bypassed + Redis mocked."""
    from routes.admin.session_bootstrap import get_redis as _get_redis

    def override_get_db():
        yield db

    def override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_admin_key] = lambda: None
    app.dependency_overrides[_get_redis] = override_get_redis
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def raw_client_with_mock_redis(db, mock_redis):
    """TestClient with DB override + Redis mocked, NO auth bypass — for 403 tests."""
    from routes.admin.session_bootstrap import get_redis as _get_redis

    def override_get_db():
        yield db

    def override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_get_redis] = override_get_redis
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(_get_redis, None)


# ---------------------------------------------------------------------------
# test_session_bootstrap_ok
# ---------------------------------------------------------------------------


class TestSessionBootstrapOk:
    def test_returns_200_with_ott_and_redirect_url(self, client_with_mock_redis) -> None:
        """POST /api/v1/admin/session-bootstrap → 200 + {ott, redirect_url}."""
        resp = client_with_mock_redis.post(
            "/api/v1/admin/session-bootstrap",
            json={"redirect": "/admin/rewards"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "ott" in body
        assert "redirect_url" in body
        assert isinstance(body["ott"], str)
        assert isinstance(body["redirect_url"], str)

    def test_ott_is_valid_hs256_jwt(self, client_with_mock_redis) -> None:
        """The OTT is a valid HS256 JWT with expected claims."""
        resp = client_with_mock_redis.post(
            "/api/v1/admin/session-bootstrap",
            json={"redirect": "/admin/rewards"},
        )
        assert resp.status_code == 200, resp.text
        ott = resp.json()["ott"]
        payload = jwt.decode(ott, _ADMIN_KEY, algorithms=["HS256"])
        assert payload["sub"] == "ott"
        assert "exp" in payload
        assert "jti" in payload
        assert payload["redirect"] == "/admin/rewards"
        uuid.UUID(payload["jti"])  # must be valid UUID


# ---------------------------------------------------------------------------
# test_session_bootstrap_wrong_key
# ---------------------------------------------------------------------------


class TestSessionBootstrapWrongKey:
    def test_wrong_key_returns_403(self, raw_client_with_mock_redis) -> None:
        """POST with wrong ADMIN_API_KEY → 403."""
        resp = raw_client_with_mock_redis.post(
            "/api/v1/admin/session-bootstrap",
            json={"redirect": "/admin/rewards"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_no_key_returns_403(self, raw_client_with_mock_redis) -> None:
        """POST without Authorization header → 403."""
        resp = raw_client_with_mock_redis.post(
            "/api/v1/admin/session-bootstrap",
            json={"redirect": "/admin/rewards"},
        )
        assert resp.status_code == 403
