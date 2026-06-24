"""Test-only helpers for minting RS256 JWTs.

NOT imported by production code — used by pytest conftests and test
modules so they no longer hand-roll ``jose.jwt.encode``. Lives in
ratis_core (not a service test tree) because every service's test suite
needs an RS256 key pair after the HS256 -> RS256 migration.

``generate_test_jwt_keypair`` produces a fresh ephemeral pair; no key is
ever committed. ``make_test_token`` signs a claims dict with a private
PEM using RS256, the algorithm production verification now enforces.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt


def generate_test_jwt_keypair() -> tuple[str, str]:
    """Generate an ephemeral RSA-2048 key pair.

    Returns ``(private_pem, public_pem)`` as PKCS8 / SubjectPublicKeyInfo
    PEM strings — the same encoding ``scripts/gen-jwt-keys.sh`` emits and
    that ``ratis_core.jwt`` / ``auth_service`` load at runtime.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def make_test_token(claims: dict, private_pem: str) -> str:
    """Sign ``claims`` into an RS256 JWT using ``private_pem``."""
    return jwt.encode(claims, private_pem, algorithm="RS256")
