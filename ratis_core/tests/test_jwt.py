"""Tests for ratis_core.jwt — RS256 verification + claim enforcement.

A token without ``exp`` never expires; one without ``iat`` bypasses the
post-password-change revocation check. decode_access_token must reject
tokens missing any of exp / iat / sub, and reject tokens signed with a
key other than the configured public key (the core RS256 property).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from ratis_core.jwt import decode_access_token
from ratis_core.testing import generate_test_jwt_keypair, make_test_token

_AUD = "ratis"
_PRIVATE_PEM, _PUBLIC_PEM = generate_test_jwt_keypair()


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch, tmp_path):
    pub = tmp_path / "jwt_public.pem"
    pub.write_text(_PUBLIC_PEM)
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("JWT_AUDIENCE", _AUD)
    import ratis_core.jwt as jwt_mod

    monkeypatch.setattr(jwt_mod, "_PUBLIC_KEY", None)


def _encode(claims: dict) -> str:
    return make_test_token(claims, _PRIVATE_PEM)


def _valid_claims(**overrides) -> dict:
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "type": "access",
        "exp": now + timedelta(minutes=15),
        "iat": now,
        "aud": _AUD,
    }
    claims.update(overrides)
    return claims


def test_valid_token_decodes():
    claims = _valid_claims()
    user_id, token_iat = decode_access_token(_encode(claims))
    assert str(user_id) == claims["sub"]
    assert token_iat is not None


def test_token_without_exp_rejected():
    claims = _valid_claims()
    del claims["exp"]
    with pytest.raises(ValueError, match="invalid_token"):
        decode_access_token(_encode(claims))


def test_token_without_iat_rejected():
    claims = _valid_claims()
    del claims["iat"]
    with pytest.raises(ValueError, match="invalid_token"):
        decode_access_token(_encode(claims))


def test_token_without_sub_rejected():
    claims = _valid_claims()
    del claims["sub"]
    with pytest.raises(ValueError, match="invalid_token"):
        decode_access_token(_encode(claims))


def test_expired_token_rejected():
    now = datetime.now(UTC)
    claims = _valid_claims(exp=now - timedelta(minutes=1), iat=now - timedelta(minutes=30))
    with pytest.raises(ValueError, match="invalid_token"):
        decode_access_token(_encode(claims))


def test_token_signed_with_wrong_key_rejected():
    """Forgery rejection — the core RS256 security property.

    A token signed with a key other than the configured public key's
    private counterpart must be rejected — that is the whole point of the
    RS256 migration: a leaked verification key cannot forge tokens.
    """
    other_private_pem, _ = generate_test_jwt_keypair()
    forged = make_test_token(_valid_claims(), other_private_pem)
    with pytest.raises(ValueError, match="invalid_token"):
        decode_access_token(forged)
