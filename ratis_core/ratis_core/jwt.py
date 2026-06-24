"""
JWT verification utilities shared across all Ratis services.

Tokens are signed by ratis_auth with an RSA private key (RS256) and
verified here with the matching public key, loaded once from the PEM
file at JWT_PUBLIC_KEY_PATH. A leaked public key cannot forge tokens —
only ratis_auth holds the private key.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from jose import JWTError, jwt

# Module-level cache — the public key never changes at runtime. Mirrors
# the import-time env cache pattern used elsewhere (gift_card_service
# _DATABASE_URL). Loaded lazily on first decode so a pytest conftest can
# write the PEM + set JWT_PUBLIC_KEY_PATH before the first call.
_PUBLIC_KEY: str | None = None


def _public_key() -> str:
    """Load + cache the verification public key PEM."""
    global _PUBLIC_KEY
    if _PUBLIC_KEY is None:
        _PUBLIC_KEY = Path(os.environ["JWT_PUBLIC_KEY_PATH"]).read_text()
    return _PUBLIC_KEY


def _jwt_audience() -> str:
    return os.environ.get("JWT_AUDIENCE", "ratis")


def decode_access_token(token: str) -> tuple[uuid.UUID, datetime | None]:
    """Decode and validate a Ratis access token. Raises ValueError on any failure."""
    try:
        # require exp/iat/sub — a token without exp never expires, and one
        # without iat would bypass post-password-change revocation.
        payload = jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            audience=_jwt_audience(),
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except JWTError:
        raise ValueError("invalid_token")
    if payload.get("type") != "access":
        raise ValueError("invalid_token")
    sub = payload.get("sub")
    if sub is None:
        raise ValueError("invalid_token")
    user_id = uuid.UUID(sub)
    iat_raw = payload.get("iat")
    token_iat: datetime | None = datetime.fromtimestamp(iat_raw, tz=UTC) if iat_raw is not None else None
    return user_id, token_iat


def decode_refresh_token(token: str) -> str:
    """Decode a Ratis refresh token and return its JTI.

    Verifies the RS256 signature with the public key, requires
    ``type == "refresh"`` and a ``jti`` claim. Raises ValueError
    ("invalid_token") on any failure — same contract as
    decode_access_token. Shared by auth_service.refresh_tokens and
    account_service.logout so refresh-token decoding lives in one place.
    """
    try:
        payload = jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            audience=_jwt_audience(),
        )
    except JWTError:
        raise ValueError("invalid_token")
    if payload.get("type") != "refresh":
        raise ValueError("invalid_token")
    jti = payload.get("jti")
    if not jti:
        raise ValueError("invalid_token")
    return jti
