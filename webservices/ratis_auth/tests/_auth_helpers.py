# webservices/ratis_auth/tests/_auth_helpers.py
"""Shared test helpers for the OAuth-only auth flow.

``register()`` (email/password) was decommissioned — tests that previously
used it as a fixture to mint a user + tokens now go through ``/oauth``.
"""

import uuid
from unittest.mock import patch


def oauth_signup(
    client,
    email: str,
    *,
    provider: str = "google",
    sub: str | None = None,
    name: str | None = None,
) -> dict:
    """Create an account through ``POST /api/v1/auth/oauth`` and return the
    parsed ``TokenResponse`` JSON (access_token / refresh_token / ...).

    ``name=None`` omits the provider name claim so the service auto-generates
    a ``Ratis_XXXX`` display name. ``sub`` defaults to a unique value so two
    calls produce two distinct accounts.
    """
    sub = sub or f"{provider}-{uuid.uuid4().hex[:12]}"
    if provider == "google":
        idinfo: dict = {"sub": sub, "email": email, "email_verified": True}
        if name is not None:
            idinfo["name"] = name
        verifier = "services.auth_service.verify_google_token"
        payload_value = idinfo
    else:  # apple
        claims: dict = {"sub": sub, "email": email, "email_verified": "true"}
        verifier = "services.auth_service.verify_apple_token"
        payload_value = claims

    with patch(verifier, return_value=payload_value):
        resp = client.post(
            "/api/v1/auth/oauth",
            json={"provider": provider, "token": "fake-token"},
        )
    assert resp.status_code == 200, f"oauth_signup failed: {resp.status_code} {resp.text}"
    return resp.json()
