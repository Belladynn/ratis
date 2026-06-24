"""Tests for ratis_core.jwt.decode_refresh_token."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from ratis_core.jwt import decode_refresh_token
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


def _refresh_claims(**overrides) -> dict:
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(days=30),
        "iat": now,
        "aud": _AUD,
    }
    claims.update(overrides)
    return claims


def test_valid_refresh_token_returns_jti():
    claims = _refresh_claims()
    assert decode_refresh_token(make_test_token(claims, _PRIVATE_PEM)) == claims["jti"]


def test_access_token_rejected_as_refresh():
    claims = _refresh_claims(type="access")
    with pytest.raises(ValueError, match="invalid_token"):
        decode_refresh_token(make_test_token(claims, _PRIVATE_PEM))


def test_refresh_token_without_jti_rejected():
    claims = _refresh_claims()
    del claims["jti"]
    with pytest.raises(ValueError, match="invalid_token"):
        decode_refresh_token(make_test_token(claims, _PRIVATE_PEM))


def test_refresh_token_signed_with_wrong_key_rejected():
    other_private_pem, _ = generate_test_jwt_keypair()
    with pytest.raises(ValueError, match="invalid_token"):
        decode_refresh_token(make_test_token(_refresh_claims(), other_private_pem))
