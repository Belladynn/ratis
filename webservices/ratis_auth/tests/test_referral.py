"""TDD — parrainage OAuth.

Ces tests couvrent la création automatique d'un referral_code lors d'une
connexion OAuth (Google ou Apple) pour un nouvel utilisateur.
"""

from unittest.mock import patch

from ratis_core.models import User
from ratis_core.models.referral import ReferralCode

# ============================================================
# OAuth génère un referral_code (A-02)
# ============================================================


def test_oauth_google_generates_referral_code(client, db):
    """Un nouvel utilisateur via Google OAuth reçoit un referral_code."""
    mock_idinfo = {
        "sub": "google-ref-uid-001",
        "email": "googleref@gmail.com",
        "name": "Google Ref User",
        "picture": "https://example.com/avatar.jpg",
        "email_verified": True,
    }
    with patch("services.auth_service.verify_google_token", return_value=mock_idinfo):
        resp = client.post("/api/v1/auth/oauth", json={"provider": "google", "token": "fake"})
    assert resp.status_code == 200

    user = db.query(User).filter(User.email == "googleref@gmail.com").first()
    rc = db.query(ReferralCode).filter(ReferralCode.user_id == user.id).first()
    assert rc is not None
    assert rc.type == "user"


def test_oauth_apple_generates_referral_code(client, db):
    """Un nouvel utilisateur via Apple OAuth reçoit un referral_code."""
    mock_claims = {
        "sub": "apple-ref-uid-001",
        "email": "appleref@privaterelay.appleid.com",
        "email_verified": "true",
    }
    with patch("services.auth_service.verify_apple_token", return_value=mock_claims):
        resp = client.post("/api/v1/auth/oauth", json={"provider": "apple", "token": "fake"})
    assert resp.status_code == 200

    user = db.query(User).filter(User.email == "appleref@privaterelay.appleid.com").first()
    rc = db.query(ReferralCode).filter(ReferralCode.user_id == user.id).first()
    assert rc is not None
