"""Tests for the shared RS256 test-key helper."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from jose import jwt
from ratis_core.testing import generate_test_jwt_keypair, make_test_token


def test_generate_keypair_returns_pem_strings():
    private_pem, public_pem = generate_test_jwt_keypair()
    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert public_pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_generate_keypair_returns_distinct_pairs():
    priv_a, pub_a = generate_test_jwt_keypair()
    priv_b, pub_b = generate_test_jwt_keypair()
    assert priv_a != priv_b
    assert pub_a != pub_b


def test_make_test_token_is_rs256_and_verifies():
    private_pem, public_pem = generate_test_jwt_keypair()
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "type": "access",
        "aud": "ratis",
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    token = make_test_token(claims, private_pem)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"], audience="ratis")
    assert decoded["sub"] == claims["sub"]
