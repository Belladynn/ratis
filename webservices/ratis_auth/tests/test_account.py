"""
Account endpoint tests — TDD.
Each test is independent; unique emails prevent cross-test collisions.
"""

from datetime import UTC

from _auth_helpers import oauth_signup

# ============================================================
# HELPERS
# ============================================================


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "password123") -> dict:
    """Mint a user + tokens via OAuth. The ``password`` arg is kept for call-site
    compatibility but ignored — Ratis is OAuth-only.
    """
    return oauth_signup(client, email)


# ============================================================
# GET /api/v1/account/profile
# ============================================================


def test_get_profile_returns_user_data(client):
    tokens = _register(client, "acc_profile_get@example.com")
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == "acc_profile_get@example.com"
    assert data["display_name"] is not None  # auto-generated at register
    assert data["display_name"].startswith("Ratis_")
    assert data["avatar_url"] is None


def test_get_profile_unauthenticated(client):
    r = client.get("/api/v1/account/profile")
    assert r.status_code == 401
    assert r.json()["detail"] == "not_authenticated"


# ============================================================
# PATCH /api/v1/account/profile
# ============================================================


def test_patch_profile_updates_display_name_and_avatar(client):
    tokens = _register(client, "acc_profile_patch@example.com")
    r = client.patch(
        "/api/v1/account/profile",
        json={"display_name": "Alice", "avatar_url": "https://cdn.example.com/avatar.png"},
        headers=_auth(tokens["access_token"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["display_name"] == "Alice"
    assert data["avatar_url"] == "https://cdn.example.com/avatar.png"


def test_patch_profile_partial_update(client):
    tokens = _register(client, "acc_profile_partial@example.com")
    r = client.patch(
        "/api/v1/account/profile",
        json={"display_name": "Bob"},
        headers=_auth(tokens["access_token"]),
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Bob"
    assert r.json()["avatar_url"] is None


def test_patch_profile_unauthenticated(client):
    r = client.patch("/api/v1/account/profile", json={"display_name": "Alice"})
    assert r.status_code == 401


# ============================================================
# GET /api/v1/account/preferences
# ============================================================


def test_get_preferences_returns_defaults(client):
    tokens = _register(client, "acc_prefs_get@example.com")
    r = client.get("/api/v1/account/preferences", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    data = r.json()
    assert data["search_radius_km"] == 5
    assert data["transport_mode"] == "driving"


def test_get_preferences_unauthenticated(client):
    r = client.get("/api/v1/account/preferences")
    assert r.status_code == 401


def test_get_preferences_is_pure_read(client, db):
    """GET /account/preferences must not commit — it serves an existing row.

    user_preferences is created at register time, so the GET handler hits an
    existing row and must be a pure read (no db.commit() in a GET handler).
    """
    from unittest.mock import patch

    tokens = _register(client, "acc_prefs_nowrite@example.com")

    with patch.object(type(db), "commit", autospec=True) as mock_commit:
        r = client.get("/api/v1/account/preferences", headers=_auth(tokens["access_token"]))

    assert r.status_code == 200
    assert not mock_commit.called, "GET /account/preferences must not call db.commit()"


# ============================================================
# PATCH /api/v1/account/preferences
# ============================================================


def test_patch_preferences_updates_fields(client):
    tokens = _register(client, "acc_prefs_patch@example.com")
    r = client.patch(
        "/api/v1/account/preferences",
        json={"search_radius_km": 10, "transport_mode": "walking"},
        headers=_auth(tokens["access_token"]),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["search_radius_km"] == 10
    assert data["transport_mode"] == "walking"


def test_patch_preferences_invalid_transport_mode(client):
    tokens = _register(client, "acc_prefs_invalid@example.com")
    r = client.patch(
        "/api/v1/account/preferences",
        json={"transport_mode": "teleport"},
        headers=_auth(tokens["access_token"]),
    )
    assert r.status_code == 422


def test_patch_preferences_empty_body_no_op(client):
    tokens = _register(client, "acc_prefs_noop@example.com")
    original = client.get("/api/v1/account/preferences", headers=_auth(tokens["access_token"])).json()
    r = client.patch("/api/v1/account/preferences", json={}, headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json()["search_radius_km"] == original["search_radius_km"]
    assert r.json()["transport_mode"] == original["transport_mode"]


def test_patch_preferences_unauthenticated(client):
    r = client.patch("/api/v1/account/preferences", json={"search_radius_km": 10})
    assert r.status_code == 401


# ============================================================
# POST /api/v1/account/logout
# ============================================================


def test_logout_ok(client):
    tokens = _register(client, "acc_logout@example.com")
    r = client.post(
        "/api/v1/account/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=_auth(tokens["access_token"]),
    )
    assert r.status_code == 200
    assert r.json()["detail"] == "logged_out"


def test_logout_invalidates_refresh_token(client):
    tokens = _register(client, "acc_logout_inv@example.com")
    client.post(
        "/api/v1/account/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=_auth(tokens["access_token"]),
    )
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_logout_unauthenticated(client):
    r = client.post("/api/v1/account/logout", json={"refresh_token": "sometoken"})
    assert r.status_code == 401


# ============================================================
# POST /api/v1/account/logout-all
# ============================================================


def test_logout_all_ok(client):
    tokens = _register(client, "acc_logout_all@example.com")
    r = client.post("/api/v1/account/logout-all", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json()["detail"] == "all_sessions_revoked"


def test_logout_all_invalidates_all_tokens(client):
    tokens = _register(client, "acc_logout_all_inv@example.com")
    # Issue a second refresh token via rotation
    tokens2 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    client.post("/api/v1/account/logout-all", headers=_auth(tokens2["access_token"]))
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens2["refresh_token"]})
    assert r.status_code == 401


def test_logout_all_unauthenticated(client):
    r = client.post("/api/v1/account/logout-all")
    assert r.status_code == 401


# ============================================================
# DELETE /api/v1/account
# ============================================================


def test_delete_account_ok(client):
    tokens = _register(client, "acc_delete@example.com")
    r = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r.status_code == 204


def test_delete_account_revokes_refresh_tokens(client):
    tokens = _register(client, "acc_delete_revoke@example.com")
    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_delete_account_unauthenticated(client):
    r = client.delete("/api/v1/account")
    assert r.status_code == 401


def test_delete_account_hard_deletes_product_favorites(client, db):
    """RGPD — product_favorites are PII-adjacent (reveal consumption habits).
    They must be hard-deleted when the user account is anonymized, not anonymized
    with the user row.
    """
    import repositories.user_repository as user_repo
    from sqlalchemy import text

    tokens = _register(client, "acc_delete_favs@example.com")
    user = user_repo.get_by_email(db, "acc_delete_favs@example.com")

    # Seed two products + two favorites for this user
    db.execute(
        text(
            "INSERT INTO products (ean, name, source) VALUES "
            "('9991111111111', 'Fav Product 1', 'off'), "
            "('9992222222222', 'Fav Product 2', 'off') "
            "ON CONFLICT (ean) DO NOTHING"
        )
    )
    db.execute(
        text(
            "INSERT INTO product_favorites (user_id, product_ean) VALUES "
            "(:uid, '9991111111111'), (:uid, '9992222222222')"
        ),
        {"uid": str(user.id)},
    )
    db.commit()

    count = db.execute(
        text("SELECT COUNT(*) FROM product_favorites WHERE user_id = :uid"),
        {"uid": str(user.id)},
    ).scalar()
    assert count == 2

    r = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r.status_code == 204

    # Favorites hard-deleted
    count = db.execute(
        text("SELECT COUNT(*) FROM product_favorites WHERE user_id = :uid"),
        {"uid": str(user.id)},
    ).scalar()
    assert count == 0

    # User is anonymized (tombstone)
    db.refresh(user)
    assert user.is_deleted is True
    assert user.email == f"deleted_{user.id}@deleted.invalid"


# ============================================================
# Nouveaux tests validés post-audit
# ============================================================


def test_oauth_signup_returns_expires_in(client):
    from services.auth_service import access_token_expire_minutes

    tokens = oauth_signup(client, "acc_expires_in@example.com")
    assert tokens["expires_in"] == access_token_expire_minutes() * 60


def test_refresh_returns_expires_in(client):
    from services.auth_service import access_token_expire_minutes

    tokens = _register(client, "acc_refresh_expires_in@example.com")
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["expires_in"] == access_token_expire_minutes() * 60


def test_oauth_signup_auto_generates_display_name(client):
    tokens = _register(client, "acc_auto_display@example.com")
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    assert r.json()["display_name"].startswith("Ratis_")


def test_oauth_signup_uses_provider_display_name(client):
    tokens = oauth_signup(client, "acc_custom_display@example.com", name="Alice")
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    assert r.json()["display_name"] == "Alice"


def test_patch_profile_empty_body_no_op(client):
    tokens = _register(client, "acc_profile_noop@example.com")
    original_name = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"])).json()["display_name"]
    r = client.patch("/api/v1/account/profile", json={}, headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json()["display_name"] == original_name


def test_patch_profile_clears_avatar_url(client):
    tokens = _register(client, "acc_profile_clear_avatar@example.com")
    client.patch(
        "/api/v1/account/profile",
        json={"avatar_url": "https://cdn.example.com/avatar.png"},
        headers=_auth(tokens["access_token"]),
    )
    r = client.patch(
        "/api/v1/account/profile",
        json={"avatar_url": None},
        headers=_auth(tokens["access_token"]),
    )
    assert r.status_code == 200
    assert r.json()["avatar_url"] is None


def test_delete_account_blocks_subsequent_requests(client):
    tokens = _register(client, "acc_delete_block@example.com")
    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    assert r.status_code == 401


# ============================================================
# IDOR — user A cannot access user B's data (A-06)
# ============================================================


def test_idor_profile_returns_own_data(client):
    """User A's token must return user A's profile, never user B's."""
    tokens_a = _register(client, "idor_a@example.com")
    _register(client, "idor_b@example.com")

    r = client.get("/api/v1/account/profile", headers=_auth(tokens_a["access_token"]))
    assert r.status_code == 200
    assert r.json()["email"] == "idor_a@example.com"


def test_idor_delete_account_only_own(client):
    """User A's token must only delete user A's account, not user B's."""
    tokens_a = _register(client, "idor_del_a@example.com")
    tokens_b = _register(client, "idor_del_b@example.com")

    # Delete user A's account with user A's token
    r = client.delete("/api/v1/account", headers=_auth(tokens_a["access_token"]))
    assert r.status_code == 204

    # User B's account is untouched
    r = client.get("/api/v1/account/profile", headers=_auth(tokens_b["access_token"]))
    assert r.status_code == 200
    assert r.json()["email"] == "idor_del_b@example.com"


def test_expires_at_present_in_token_response(client):
    """TokenResponse must include expires_at (absolute UTC timestamp)."""
    from datetime import datetime

    tokens = oauth_signup(client, "acc_expires_at@example.com")
    assert "expires_at" in tokens
    dt = datetime.fromisoformat(tokens["expires_at"])
    assert dt.tzinfo is not None


# ============================================================
# password_changed_at — révocation access tokens (DA-16)
# ============================================================


def test_old_token_rejected_after_password_changed_at(client, db):
    """Token with iat strictly before users.password_changed_at → 401."""
    import os
    from datetime import datetime, timedelta
    from pathlib import Path

    import repositories.user_repository as user_repo
    from ratis_core.testing import make_test_token

    oauth_signup(client, "acc_pca_old_rejected@example.com")
    user = user_repo.get_by_email(db, "acc_pca_old_rejected@example.com")
    pca = datetime.now(UTC).replace(microsecond=0)
    user.password_changed_at = pca
    db.commit()

    past_iat = pca - timedelta(seconds=5)
    payload = {
        "sub": str(user.id),
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        "iat": past_iat,
        "aud": os.environ.get("JWT_AUDIENCE", "ratis"),
    }
    private_pem = Path(os.environ["JWT_PRIVATE_KEY_PATH"]).read_text()
    stale_token = make_test_token(payload, private_pem)
    resp = client.get("/api/v1/account/profile", headers=_auth(stale_token))
    assert resp.status_code == 401


def test_new_token_valid_after_password_changed_at(client, db):
    """Token with iat at/after users.password_changed_at → accepted."""
    import os
    from datetime import datetime, timedelta
    from pathlib import Path

    import repositories.user_repository as user_repo
    from ratis_core.testing import make_test_token

    oauth_signup(client, "acc_pca_new_valid@example.com")
    user = user_repo.get_by_email(db, "acc_pca_new_valid@example.com")
    pca = datetime.now(UTC).replace(microsecond=0)
    user.password_changed_at = pca
    db.commit()

    payload = {
        "sub": str(user.id),
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=15),
        "iat": pca,
        "aud": os.environ.get("JWT_AUDIENCE", "ratis"),
    }
    private_pem = Path(os.environ["JWT_PRIVATE_KEY_PATH"]).read_text()
    fresh_token = make_test_token(payload, private_pem)
    resp = client.get("/api/v1/account/profile", headers=_auth(fresh_token))
    assert resp.status_code == 200
