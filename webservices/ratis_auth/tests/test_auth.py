from pathlib import Path
from unittest.mock import patch

import pytest
from _auth_helpers import oauth_signup
from fastapi.testclient import TestClient
from main import app
from ratis_core.database import get_db

# ============================================================
# OAUTH — endpoint unifié /oauth?provider=google|apple
# ============================================================


def test_oauth_google_success(client):
    mock_idinfo = {
        "sub": "google-uid-123",
        "email": "googleuser@gmail.com",
        "name": "Google User",
        "picture": "https://example.com/avatar.jpg",
        "email_verified": True,
    }
    with patch("services.auth_service.verify_google_token", return_value=mock_idinfo):
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "fake-google-token"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_oauth_google_invalid_token(client):
    with patch("services.auth_service.verify_google_token", side_effect=ValueError("bad token")):
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "bad-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_oauth_token"


def test_oauth_google_missing_email(client):
    """Google token without email scope returns 401 with generic opaque code."""
    mock_idinfo = {"sub": "google-uid-noemail-999"}  # no 'email' key
    with patch("services.auth_service.verify_google_token", return_value=mock_idinfo):
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "fake-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_oauth_token"


def test_oauth_google_upstream_error(client):
    from services.auth_service import UpstreamServiceError

    with patch("services.auth_service.oauth_google", side_effect=UpstreamServiceError("google_auth_unavailable")):
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "any"})
    assert response.status_code == 503
    assert response.json()["detail"] == "upstream_service_error"


def test_oauth_google_same_email_different_provider_is_separate_account(client, db):
    """Google OAuth reusing an Apple user's email creates a SEPARATE account.

    H2 Phase 2 dropped the email auto-link: resolution is strictly by
    ``(provider, provider_id)`` via ``user_identities``. A google login with
    an email already used by an apple account must NOT re-point that apple
    account — it mints its own ``users`` + ``user_identities`` rows.
    """
    from ratis_core.models import User, UserIdentity

    oauth_signup(client, "linked@example.com", provider="apple")
    mock_idinfo = {
        "sub": "google-uid-linked-123",
        "email": "linked@example.com",
        "email_verified": True,
        "name": "Linked User",
        "picture": "https://example.com/pic.jpg",
    }
    with patch("services.auth_service.verify_google_token", return_value=mock_idinfo):
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "fake-token"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data

    users = db.query(User).filter(User.email == "linked@example.com").all()
    assert len(users) == 2, "google login must NOT re-point the apple account — two separate users"
    identities = (
        db.query(UserIdentity)
        .join(User, User.id == UserIdentity.user_id)
        .filter(User.email == "linked@example.com")
        .all()
    )
    assert len(identities) == 2
    assert {i.provider for i in identities} == {"google", "apple"}


def test_oauth_apple_same_email_different_provider_is_separate_account(client, db):
    """Apple OAuth reusing a Google user's email creates a SEPARATE account.

    Mirror of the google case — no email auto-link, ``(provider,
    provider_id)`` is the only identity key.
    """
    from ratis_core.models import User, UserIdentity

    oauth_signup(client, "apple_linked@example.com", provider="google")
    mock_claims = {
        "sub": "apple-uid-linked-456",
        "email": "apple_linked@example.com",
        "email_verified": "true",
    }
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "fake-token"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data

    users = db.query(User).filter(User.email == "apple_linked@example.com").all()
    assert len(users) == 2, "apple login must NOT re-point the google account — two separate users"
    identities = (
        db.query(UserIdentity)
        .join(User, User.id == UserIdentity.user_id)
        .filter(User.email == "apple_linked@example.com")
        .all()
    )
    assert len(identities) == 2
    assert {i.provider for i in identities} == {"google", "apple"}


def test_oauth_same_provider_id_returns_same_account(client, db):
    """Two /oauth calls with the same (provider, sub) resolve to one account.

    The second call carries a DIFFERENT email — resolution is strictly by
    ``(provider, provider_id)`` via ``user_identities``, never by email.
    """
    from sqlalchemy import text

    with patch(
        "services.auth_service.verify_google_token",
        return_value={"sub": "g-fixed", "email": "a@x.z", "email_verified": True},
    ):
        first = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "tok-1"})
    assert first.status_code == 200

    with patch(
        "services.auth_service.verify_google_token",
        return_value={"sub": "g-fixed", "email": "different@x.z", "email_verified": True},
    ):
        second = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "tok-2"})
    assert second.status_code == 200

    rows = db.execute(
        text(
            "SELECT u.id FROM users u "
            "JOIN user_identities i ON i.user_id = u.id "
            "WHERE i.provider = 'google' AND i.provider_id = 'g-fixed'"
        )
    ).fetchall()
    assert len(rows) == 1, "same (provider, provider_id) must map to exactly one account"


def test_oauth_different_provider_same_email_creates_separate_accounts(client, db):
    """Google + Apple sharing one email produce two distinct accounts.

    No email auto-link (spec §4.2) — each account stores the same real
    email, which is legal because ``users.email`` is no longer UNIQUE.
    """
    from sqlalchemy import text

    with patch(
        "services.auth_service.verify_google_token",
        return_value={"sub": "g-1", "email": "shared@x.z", "email_verified": True},
    ):
        google = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "g-tok"})
    assert google.status_code == 200

    with patch(
        "services.auth_service.verify_apple_token",
        return_value={"sub": "ap-1", "email": "shared@x.z", "email_verified": "true"},
    ):
        apple = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "ap-tok"})
    assert apple.status_code == 200

    user_rows = db.execute(text("SELECT id, email FROM users WHERE email = 'shared@x.z'")).fetchall()
    assert len(user_rows) == 2, "two providers, same email → two separate accounts"
    assert {r.email for r in user_rows} == {"shared@x.z"}, (
        "both accounts must store the identical real email — no sentinel/synthetic"
    )

    identity_rows = db.execute(
        text(
            "SELECT provider, provider_id FROM user_identities i "
            "JOIN users u ON u.id = i.user_id WHERE u.email = 'shared@x.z'"
        )
    ).fetchall()
    assert len(identity_rows) == 2
    assert {r.provider for r in identity_rows} == {"google", "apple"}


def test_oauth_signup_creates_identity_row(client, db):
    """A fresh signup writes the matching user_identities row."""
    from sqlalchemy import text

    with patch(
        "services.auth_service.verify_google_token",
        return_value={"sub": "g-newid", "email": "newid@x.z", "email_verified": True},
    ):
        resp = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "tok"})
    assert resp.status_code == 200

    row = db.execute(
        text(
            "SELECT i.provider, i.provider_id, i.user_id, u.email AS user_email "
            "FROM user_identities i JOIN users u ON u.id = i.user_id "
            "WHERE i.provider = 'google' AND i.provider_id = 'g-newid'"
        )
    ).fetchone()
    assert row is not None, "signup must create a user_identities row"
    assert row.user_email == "newid@x.z"
    assert row.user_id is not None


def test_oauth_apple_success(client):
    mock_claims = {
        "sub": "apple-uid-456",
        "email": "appleuser@privaterelay.appleid.com",
        "email_verified": "true",
    }
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "fake-apple-token"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_oauth_apple_invalid_token(client):
    with patch("services.auth_service.verify_apple_token", side_effect=ValueError("bad token")):
        response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "bad-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_oauth_token"


def test_oauth_apple_upstream_error(client):
    from services.auth_service import UpstreamServiceError

    with patch("services.auth_service.oauth_apple", side_effect=UpstreamServiceError("apple_auth_unavailable")):
        response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "any"})
    assert response.status_code == 503
    assert response.json()["detail"] == "upstream_service_error"


def test_oauth_apple_missing_email(client):
    """Apple token without email field returns 401 with generic opaque code."""
    mock_claims = {"sub": "apple-uid-noemail-789"}  # no 'email' key
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "fake-apple-no-email"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_oauth_token"


def test_oauth_google_rejects_unverified_email(client):
    """Google token with email_verified=False is rejected (401)."""
    mock_idinfo = {
        "sub": "google-unverified-1",
        "email": "unverified@gmail.com",
        "email_verified": False,
        "name": "Unverified User",
    }
    with patch("services.auth_service.verify_google_token", return_value=mock_idinfo):
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "fake-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_oauth_token"


def test_oauth_apple_rejects_unverified_email(client):
    """Apple token with email_verified='false' is rejected (401)."""
    mock_claims = {
        "sub": "apple-unverified-1",
        "email": "unverified@privaterelay.appleid.com",
        "email_verified": "false",
    }
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "fake-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_oauth_token"


def test_oauth_invalid_provider(client):
    """Unknown provider is rejected at validation level."""
    response = client.post("/api/v1/auth/oauth", json={"provider": "facebook", "token": "any"})
    assert response.status_code == 422


def test_oauth_apple_disabled_when_client_id_absent(client, monkeypatch):
    """Apple sign-in disabled (Android-only build) → 503, no KeyError 500."""
    monkeypatch.delenv("APPLE_CLIENT_ID", raising=False)
    response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "any"})
    assert response.status_code == 503
    assert response.json()["detail"] == "upstream_service_error"


def test_oauth_apple_disabled_when_client_id_blank(client, monkeypatch):
    """Blank APPLE_CLIENT_ID is treated as disabled, not as a valid empty audience."""
    monkeypatch.setenv("APPLE_CLIENT_ID", "")
    response = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "any"})
    assert response.status_code == 503
    assert response.json()["detail"] == "upstream_service_error"


# ============================================================
# verify_apple_token — JWT-level claim verification (audit F-AU-1)
# ============================================================


def _make_apple_test_jwk_and_signer():
    """
    Generate an RSA keypair and return (jwk_public_dict, sign_callable).

    The JWK dict is the shape Apple's JWKS endpoint returns
    (kty/kid/use/alg/n/e), suitable for `_get_apple_jwks` monkeypatch.
    The signer mints a JWS for given claims using the private key.
    """
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwt as jose_jwt

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64u_uint(value: int) -> str:
        byte_len = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(byte_len, "big")).rstrip(b"=").decode()

    kid = "test-apple-kid-1"
    jwk_public = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u_uint(public_numbers.n),
        "e": _b64u_uint(public_numbers.e),
    }

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    def sign(claims: dict) -> str:
        return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})

    return jwk_public, sign


def test_verify_apple_token_accepts_correct_issuer(monkeypatch):
    """Valid Apple JWT with iss=https://appleid.apple.com is accepted."""
    import time

    from services import auth_service

    jwk_public, sign = _make_apple_test_jwk_and_signer()
    monkeypatch.setattr(auth_service, "_get_apple_jwks", lambda force_refresh=False: [jwk_public])

    now = int(time.time())
    token = sign(
        {
            "iss": "https://appleid.apple.com",
            "aud": "test-apple-client-id",
            "sub": "apple-user-correct-iss",
            "email": "user@privaterelay.appleid.com",
            "iat": now,
            "exp": now + 600,
        }
    )

    claims = auth_service.verify_apple_token(token)
    assert claims["sub"] == "apple-user-correct-iss"
    assert claims["iss"] == "https://appleid.apple.com"


def test_verify_apple_token_rejects_wrong_issuer(monkeypatch):
    """Apple JWT with a forged iss (not appleid.apple.com) is rejected — F-AU-1 fix."""
    import time

    from jose import JWTError
    from services import auth_service

    jwk_public, sign = _make_apple_test_jwk_and_signer()
    monkeypatch.setattr(auth_service, "_get_apple_jwks", lambda force_refresh=False: [jwk_public])

    now = int(time.time())
    token = sign(
        {
            "iss": "https://evil.example.com",
            "aud": "test-apple-client-id",
            "sub": "apple-user-wrong-iss",
            "email": "attacker@evil.example.com",
            "iat": now,
            "exp": now + 600,
        }
    )

    with pytest.raises(JWTError):
        auth_service.verify_apple_token(token)


def test_verify_apple_token_rejects_missing_issuer(monkeypatch):
    """Apple JWT without an iss claim is rejected — F-AU-1 fix."""
    import time

    from jose import JWTError
    from services import auth_service

    jwk_public, sign = _make_apple_test_jwk_and_signer()
    monkeypatch.setattr(auth_service, "_get_apple_jwks", lambda force_refresh=False: [jwk_public])

    now = int(time.time())
    token = sign(
        {
            # iss intentionally omitted
            "aud": "test-apple-client-id",
            "sub": "apple-user-no-iss",
            "email": "user@privaterelay.appleid.com",
            "iat": now,
            "exp": now + 600,
        }
    )

    with pytest.raises(JWTError):
        auth_service.verify_apple_token(token)


# ============================================================
# ME
# ============================================================


def test_me_success(client):
    tokens = oauth_signup(client, "frank@example.com", name="Frank")
    token = tokens["access_token"]
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "frank@example.com"
    assert data["account_type"] == "oauth"


def test_me_returns_support_id_with_correct_format(client):
    """``/auth/me`` exposes the public ``support_id`` for the profile screen."""
    import re

    tokens = oauth_signup(client, "sid-me@example.com")
    token = tokens["access_token"]
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert "support_id" in data
    assert re.match(r"^RTS-[A-HJ-NP-Z2-9]{6}$", data["support_id"]), f"unexpected format : {data['support_id']!r}"


def test_oauth_signup_assigns_unique_support_id(client, db):
    """Two OAuth signups ⇒ two distinct support_ids in the DB."""
    from sqlalchemy import text

    oauth_signup(client, "sid1@example.com")
    oauth_signup(client, "sid2@example.com")

    rows = db.execute(
        text("SELECT support_id FROM users WHERE email IN ('sid1@example.com', 'sid2@example.com')")
    ).fetchall()
    sids = [r.support_id for r in rows]
    assert len(sids) == 2
    assert len(set(sids)) == 2  # distinct
    for sid in sids:
        assert sid is not None
        assert sid.startswith("RTS-")


def test_me_missing_token(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_invalid_token(client):
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert response.status_code == 401


# ============================================================
# REFRESH
# ============================================================


def test_refresh_success(client):
    tokens = oauth_signup(client, "grace@example.com")
    refresh_token = tokens["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_refresh_rotates_token(client):
    """Each /refresh call issues a new refresh token (old one must not be reused)."""
    tokens = oauth_signup(client, "rotate@example.com")
    original_refresh = tokens["refresh_token"]

    first = client.post("/api/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert first.status_code == 200
    new_refresh = first.json()["refresh_token"]
    assert new_refresh != original_refresh

    # Original token is now revoked — must be rejected
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert second.status_code == 401


def test_refresh_invalid_token(client):
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "bad.token.here"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_refresh_token"


def test_refresh_with_access_token_rejected(client):
    """Access token must not be accepted as a refresh token."""
    tokens = oauth_signup(client, "henry@example.com")
    access_token = tokens["access_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401


# ============================================================
# RATE LIMITING
# ============================================================


def test_oauth_rate_limited(client):
    """OAuth endpoint is rate-limited to 5 requests/minute/IP."""
    with patch("services.auth_service.verify_google_token", side_effect=ValueError("bad")):
        for _ in range(5):
            client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "any"})
        response = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "any"})
    assert response.status_code == 429
    assert response.json()["detail"] == "rate_limit_exceeded"


def test_refresh_rate_limited(client):
    """/refresh is rate-limited to 5 requests/minute/IP — brute-force guard."""
    payload = {"refresh_token": "bad.token.here"}
    for _ in range(5):
        client.post("/api/v1/auth/refresh", json=payload)
    response = client.post("/api/v1/auth/refresh", json=payload)
    assert response.status_code == 429
    assert response.json()["detail"] == "rate_limit_exceeded"


# ------------------------------------------------------------
# Audit F-AU-4 — slowapi must bucket per real client IP.
#
# In prod, uvicorn must be launched with `--forwarded-allow-ips '*'` so the
# ProxyHeadersMiddleware rewrites scope['client'] from the X-Forwarded-For
# header set by Caddy. Without that, scope['client'] stays as the Caddy
# container IP for ALL traffic → slowapi `get_remote_address(request)` sees
# the same IP for every request → global rate-limit bucket → brute-force
# defenses neutralized.
#
# These tests verify the slowapi layer behaves correctly *given* that
# scope['client'] reflects the real client. The Dockerfile assertion test
# below locks in the uvicorn config that ensures scope['client'] is rewritten
# from X-Forwarded-For in prod.
# ------------------------------------------------------------


def _client_for_ip(db, ip: str) -> TestClient:
    """Build a TestClient whose ASGI scope reports `ip` as the client host.

    Mimics what uvicorn does once `--forwarded-allow-ips '*'` is set and a
    trusted proxy supplies X-Forwarded-For: ProxyHeadersMiddleware rewrites
    scope['client'] before any app middleware runs.
    """

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, client=(ip, 12345))


def test_rate_limit_buckets_per_client_ip(db):
    """slowapi must bucket per request.client.host, not globally."""
    try:
        c_a = _client_for_ip(db, "10.0.0.1")
        with patch("services.auth_service.verify_google_token", side_effect=ValueError("bad")):
            for i in range(5):
                r = c_a.post(
                    "/api/v1/auth/oauth",
                    json={"provider": "google", "token": f"tok-a-{i}"},
                )
                assert r.status_code != 429, f"IP A request {i + 1} unexpectedly 429"
            r = c_a.post(
                "/api/v1/auth/oauth",
                json={"provider": "google", "token": "tok-a-6"},
            )
            assert r.status_code == 429, "IP A 6th request should be rate-limited"

            c_b = _client_for_ip(db, "10.0.0.2")
            r = c_b.post(
                "/api/v1/auth/oauth",
                json={"provider": "google", "token": "tok-b-1"},
            )
            assert r.status_code != 429, (
                "IP B was rate-limited despite IP A exhausting its bucket — "
                "rate limit is global, not per-IP (F-AU-4 regression)."
            )
    finally:
        app.dependency_overrides.clear()


def test_rate_limit_blocks_brute_force_from_same_ip(db):
    """Six requests from the same IP must trigger 429 on the 6th."""
    try:
        c = _client_for_ip(db, "10.0.0.3")
        with patch("services.auth_service.verify_google_token", side_effect=ValueError("bad")):
            for i in range(5):
                c.post("/api/v1/auth/oauth", json={"provider": "google", "token": f"bf-{i}"})
            r = c.post("/api/v1/auth/oauth", json={"provider": "google", "token": "bf-6"})
        assert r.status_code == 429
        assert r.json()["detail"] == "rate_limit_exceeded"
    finally:
        app.dependency_overrides.clear()


def test_dockerfile_sets_forwarded_allow_ips():
    """Audit F-AU-4 regression guard.

    The ratis_auth Dockerfile MUST launch uvicorn with --forwarded-allow-ips
    so ProxyHeadersMiddleware rewrites scope['client'] from X-Forwarded-For
    set by Caddy. Otherwise slowapi sees the proxy IP for all requests and
    rate limiting becomes a global bucket.
    """
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    content = dockerfile.read_text()
    assert "--proxy-headers" in content, "uvicorn must run with --proxy-headers"
    assert "--forwarded-allow-ips" in content, (
        "uvicorn must run with --forwarded-allow-ips to honor X-Forwarded-For from Caddy (audit F-AU-4)"
    )
