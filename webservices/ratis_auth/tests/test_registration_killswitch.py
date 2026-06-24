"""Registration kill-switch — gate the OAuth *create* path behind a flag.

The flag ``auth.registrations_open`` (settings, default ``True``) only
guards account *creation*. An OAuth login that resolves an existing
identity must keep working even when registrations are closed — the gate
sits strictly on the create branch of ``_resolve_or_create_oauth_user``.

When closed, the service raises ``RegistrationsClosedError`` which the
``/oauth`` route maps to HTTP 503 ``registrations_closed`` (distinct from
the generic ``upstream_service_error``).
"""

from unittest.mock import patch

from _auth_helpers import oauth_signup


def _settings_with_registrations(open_: bool) -> dict:
    """Minimal settings dict carrying only the auth section under test."""
    return {"auth": {"registrations_open": open_}}


def test_new_user_blocked_when_registrations_closed(client):
    """A brand-new OAuth user is rejected with 503 ``registrations_closed``."""
    mock_idinfo = {
        "sub": "google-newuser-closed-1",
        "email": "newcomer@example.com",
        "email_verified": True,
        "name": "New Comer",
    }
    with (
        patch(
            "services.auth_service.load_settings",
            return_value=_settings_with_registrations(False),
        ),
        patch("services.auth_service.verify_google_token", return_value=mock_idinfo),
    ):
        response = client.post(
            "/api/v1/auth/oauth",
            json={"provider": "google", "token": "fake-token"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "registrations_closed"


def test_new_user_not_persisted_when_registrations_closed(client, db):
    """The blocked signup must not leave a half-created account behind."""
    from sqlalchemy import text

    mock_idinfo = {
        "sub": "google-newuser-closed-2",
        "email": "notpersisted@example.com",
        "email_verified": True,
        "name": "Ghost",
    }
    with (
        patch(
            "services.auth_service.load_settings",
            return_value=_settings_with_registrations(False),
        ),
        patch("services.auth_service.verify_google_token", return_value=mock_idinfo),
    ):
        response = client.post(
            "/api/v1/auth/oauth",
            json={"provider": "google", "token": "fake-token"},
        )
    assert response.status_code == 503

    rows = db.execute(
        text("SELECT 1 FROM user_identities WHERE provider = 'google' AND provider_id = 'google-newuser-closed-2'")
    ).fetchall()
    assert rows == [], "no identity row may be created when registrations are closed"


def test_existing_user_logs_in_when_registrations_closed(client):
    """An existing identity still authenticates (resolve path is NOT gated)."""
    # First sign up while registrations are open (default JSON config).
    oauth_signup(client, "returning@example.com", sub="google-returning-1")

    # Now registrations are closed — the SAME identity must still log in.
    mock_idinfo = {
        "sub": "google-returning-1",
        "email": "returning@example.com",
        "email_verified": True,
        "name": "Returning User",
    }
    with (
        patch(
            "services.auth_service.load_settings",
            return_value=_settings_with_registrations(False),
        ),
        patch("services.auth_service.verify_google_token", return_value=mock_idinfo),
    ):
        response = client.post(
            "/api/v1/auth/oauth",
            json={"provider": "google", "token": "fake-token"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_new_user_created_when_registrations_open_default(client, db):
    """With the default flag (TRUE), a new OAuth signup is created normally."""
    from sqlalchemy import text

    mock_idinfo = {
        "sub": "google-newuser-open-1",
        "email": "freshopen@example.com",
        "email_verified": True,
        "name": "Fresh Open",
    }
    # No load_settings patch — relies on the real default (registrations_open: true).
    with patch("services.auth_service.verify_google_token", return_value=mock_idinfo):
        response = client.post(
            "/api/v1/auth/oauth",
            json={"provider": "google", "token": "fake-token"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data

    row = db.execute(
        text(
            "SELECT u.email FROM user_identities i JOIN users u ON u.id = i.user_id "
            "WHERE i.provider = 'google' AND i.provider_id = 'google-newuser-open-1'"
        )
    ).fetchone()
    assert row is not None, "signup must create the account when registrations are open"
    assert row.email == "freshopen@example.com"


def test_apple_new_user_blocked_when_registrations_closed(client):
    """The gate covers Apple sign-in too (shared create path)."""
    mock_claims = {
        "sub": "apple-newuser-closed-1",
        "email": "appleclosed@privaterelay.appleid.com",
        "email_verified": "true",
    }
    with (
        patch(
            "services.auth_service.load_settings",
            return_value=_settings_with_registrations(False),
        ),
        patch("services.auth_service.verify_apple_token", return_value=mock_claims),
    ):
        response = client.post(
            "/api/v1/auth/oauth",
            json={"provider": "apple", "token": "fake-token"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "registrations_closed"
