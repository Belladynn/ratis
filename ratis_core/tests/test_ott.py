"""TDD coverage for ratis_core.ott — OTT JWT make/validate (Module 10, PR 5).

Tests enforce:
- Correct claims (sub, exp, jti, redirect)
- Valid token decodes to OTTClaims
- Expired token raises ValueError
- Wrong key raises ValueError
"""

from __future__ import annotations

import time
import uuid

import pytest
from jose import jwt
from ratis_core.ott import OTTClaims, make_ott_jwt, validate_ott_jwt

_ADMIN_KEY = "test-admin-key-padded-to-32-chars-min"  # pragma: allowlist secret
_ALT_KEY = "another-key-padded-to-32-chars-min!"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# test_make_ott_jwt_valid_claims
# ---------------------------------------------------------------------------


class TestMakeOttJwt:
    def test_valid_claims_present(self) -> None:
        """make_ott_jwt returns a JWT with sub='ott', exp, jti, redirect claims."""
        redirect = "/admin/db-approvals"
        token = make_ott_jwt(_ADMIN_KEY, redirect=redirect)
        # Decode WITHOUT verification so we can inspect raw claims.
        payload = jwt.decode(token, _ADMIN_KEY, algorithms=["HS256"])
        assert payload["sub"] == "ott"
        assert "exp" in payload
        assert "jti" in payload
        assert payload["redirect"] == redirect
        # jti must be a valid UUID4
        jti = uuid.UUID(payload["jti"])
        assert jti.version == 4

    def test_default_ttl_is_60s(self) -> None:
        """Token expiry defaults to ~60 seconds from now."""
        token = make_ott_jwt(_ADMIN_KEY, redirect="/admin/foo")
        payload = jwt.decode(token, _ADMIN_KEY, algorithms=["HS256"])
        now = int(time.time())
        # exp should be within [now+55, now+65] — allow 5s of clock drift
        assert now + 55 <= payload["exp"] <= now + 65

    def test_custom_ttl(self) -> None:
        """Custom ttl_sec is respected."""
        token = make_ott_jwt(_ADMIN_KEY, redirect="/admin/foo", ttl_sec=120)
        payload = jwt.decode(token, _ADMIN_KEY, algorithms=["HS256"])
        now = int(time.time())
        assert now + 115 <= payload["exp"] <= now + 125

    def test_unique_jti_per_call(self) -> None:
        """Each call generates a distinct jti (UUIDs are not reused)."""
        t1 = make_ott_jwt(_ADMIN_KEY, redirect="/admin/a")
        t2 = make_ott_jwt(_ADMIN_KEY, redirect="/admin/a")
        p1 = jwt.decode(t1, _ADMIN_KEY, algorithms=["HS256"])
        p2 = jwt.decode(t2, _ADMIN_KEY, algorithms=["HS256"])
        assert p1["jti"] != p2["jti"]


# ---------------------------------------------------------------------------
# test_validate_ott_jwt_ok
# ---------------------------------------------------------------------------


class TestValidateOttJwtOk:
    def test_valid_token_returns_claims(self) -> None:
        """validate_ott_jwt returns OTTClaims for a valid, fresh token."""
        redirect = "/admin/db-approvals"
        token = make_ott_jwt(_ADMIN_KEY, redirect=redirect)
        claims = validate_ott_jwt(token, _ADMIN_KEY)
        assert isinstance(claims, OTTClaims)
        assert claims.sub == "ott"
        assert claims.redirect == redirect
        assert isinstance(claims.jti, str)
        # jti is a valid UUID
        uuid.UUID(claims.jti)


# ---------------------------------------------------------------------------
# test_validate_ott_jwt_expired
# ---------------------------------------------------------------------------


class TestValidateOttJwtExpired:
    def test_expired_token_raises(self) -> None:
        """validate_ott_jwt raises ValueError for an expired token (exp in the past)."""
        # ttl_sec=1 so we can't just sleep — instead forge a token with past exp.
        now = int(time.time())
        payload = {
            "sub": "ott",
            "exp": now - 10,  # 10 seconds in the past
            "jti": str(uuid.uuid4()),
            "redirect": "/admin/x",
        }
        expired_token = jwt.encode(payload, _ADMIN_KEY, algorithm="HS256")
        with pytest.raises(ValueError, match="invalid_ott"):
            validate_ott_jwt(expired_token, _ADMIN_KEY)


# ---------------------------------------------------------------------------
# test_validate_ott_jwt_wrong_key
# ---------------------------------------------------------------------------


class TestValidateOttJwtWrongKey:
    def test_wrong_key_raises(self) -> None:
        """validate_ott_jwt raises ValueError when the token was signed with a different key."""
        token = make_ott_jwt(_ADMIN_KEY, redirect="/admin/x")
        with pytest.raises(ValueError, match="invalid_ott"):
            validate_ott_jwt(token, _ALT_KEY)

    def test_tampered_token_raises(self) -> None:
        """A modified token (wrong signature) is rejected."""
        token = make_ott_jwt(_ADMIN_KEY, redirect="/admin/x")
        # Flip the last character of the signature.
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(ValueError, match="invalid_ott"):
            validate_ott_jwt(tampered, _ADMIN_KEY)
