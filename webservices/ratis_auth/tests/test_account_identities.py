"""Tests for GET /api/v1/account/identities — list linked OAuth identities."""

from unittest.mock import patch

import repositories.user_identity_repository as identity_repo
from _auth_helpers import oauth_signup


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_identities_lists_linked_providers(client):
    tokens = oauth_signup(client, "identities_list@example.com")
    r = client.get("/api/v1/account/identities", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["provider"] == "google"


# ============================================================
# POST /api/v1/account/link-provider
# ============================================================


def test_link_provider_attaches_identity(client):
    tokens = oauth_signup(client, "link_base@example.com")
    mock_claims = {"sub": "ap-link-1", "email": "apple@x.z", "email_verified": "true"}
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        r = client.post(
            "/api/v1/account/link-provider",
            json={"provider": "apple", "token": "fake"},
            headers=_auth(tokens["access_token"]),
        )
    assert r.status_code == 200

    r2 = client.get("/api/v1/account/identities", headers=_auth(tokens["access_token"]))
    assert r2.status_code == 200
    assert len(r2.json()) == 2


def test_link_provider_already_linked_elsewhere_errors(client):
    tokens_a = oauth_signup(client, "link_a@example.com")
    oauth_signup(client, "link_b@example.com", provider="apple", sub="ap-b")

    mock_claims = {"sub": "ap-b", "email": "link_b@example.com", "email_verified": "true"}
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        r = client.post(
            "/api/v1/account/link-provider",
            json={"provider": "apple", "token": "fake"},
            headers=_auth(tokens_a["access_token"]),
        )
    assert r.status_code == 409
    assert r.json()["detail"] == "identity_already_linked"


def test_link_provider_same_identity_idempotent(client, db):
    tokens = oauth_signup(client, "link_idem@example.com", provider="apple", sub="ap-idem")

    from ratis_core.jwt import decode_access_token

    user_id, _ = decode_access_token(tokens["access_token"])
    before = identity_repo.count_for_user(db, user_id)

    mock_claims = {"sub": "ap-idem", "email": "link_idem@example.com", "email_verified": "true"}
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        r = client.post(
            "/api/v1/account/link-provider",
            json={"provider": "apple", "token": "fake"},
            headers=_auth(tokens["access_token"]),
        )
    assert r.status_code == 200
    assert identity_repo.count_for_user(db, user_id) == before


def test_link_provider_invalid_token(client):
    tokens = oauth_signup(client, "link_invalid@example.com")
    with patch("services.auth_service.verify_apple_token", side_effect=ValueError("bad token")):
        r = client.post(
            "/api/v1/account/link-provider",
            json={"provider": "apple", "token": "fake"},
            headers=_auth(tokens["access_token"]),
        )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_oauth_token"


# ============================================================
# DELETE /api/v1/account/identities/{provider}
# ============================================================


def test_unlink_provider_removes_identity(client):
    tokens = oauth_signup(client, "unlink_base@example.com")
    mock_claims = {"sub": "ap-unlink-1", "email": "apple@x.z", "email_verified": "true"}
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        r = client.post(
            "/api/v1/account/link-provider",
            json={"provider": "apple", "token": "fake"},
            headers=_auth(tokens["access_token"]),
        )
    assert r.status_code == 200

    r2 = client.delete("/api/v1/account/identities/apple", headers=_auth(tokens["access_token"]))
    assert r2.status_code == 200

    r3 = client.get("/api/v1/account/identities", headers=_auth(tokens["access_token"]))
    assert r3.status_code == 200
    body = r3.json()
    assert len(body) == 1
    assert body[0]["provider"] == "google"


def test_unlink_last_identity_rejected(client):
    tokens = oauth_signup(client, "unlink_last@example.com")
    r = client.delete("/api/v1/account/identities/google", headers=_auth(tokens["access_token"]))
    assert r.status_code == 409
    assert r.json()["detail"] == "cannot_unlink_last_identity"


def test_unlink_provider_not_linked_returns_404(client):
    tokens = oauth_signup(client, "unlink_missing@example.com")
    r = client.delete("/api/v1/account/identities/apple", headers=_auth(tokens["access_token"]))
    assert r.status_code == 404
    assert r.json()["detail"] == "identity_not_found"
