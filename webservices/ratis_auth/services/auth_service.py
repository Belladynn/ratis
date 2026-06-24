import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import repositories.preferences_repository as prefs_repo
import repositories.referral_repository as referral_repo
import repositories.refresh_token_repository as refresh_token_repo
import repositories.user_identity_repository as identity_repo
import repositories.user_repository as user_repo
from google.auth import exceptions as google_exc
from jose import JWTError, jwt
from ratis_core.jwt import decode_refresh_token
from ratis_core.settings import load_settings
from sqlalchemy.orm import Session


def _create_cab_balance(db: Session, user_id: uuid.UUID) -> None:
    """Create a UserCabBalance row for a newly registered user (balance = 0)."""
    from ratis_core.models.gamification import UserCabBalance

    db.add(UserCabBalance(user_id=user_id, balance=0))


def _create_cashback_balance(db: Session, user_id: uuid.UUID) -> None:
    """Create a UserCashbackBalance row for a newly registered user (balance = 0)."""
    from ratis_core.models.gamification import UserCashbackBalance

    db.add(UserCashbackBalance(user_id=user_id, balance=0))


APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"


# Module-level cache — the private key never changes at runtime. Loaded
# lazily on first sign so a pytest conftest can write the PEM + set
# JWT_PRIVATE_KEY_PATH before the first token is minted.
_PRIVATE_KEY: str | None = None


def _jwt_private_key() -> str:
    """Load + cache the RS256 signing private key PEM."""
    global _PRIVATE_KEY
    if _PRIVATE_KEY is None:
        from pathlib import Path

        _PRIVATE_KEY = Path(os.environ["JWT_PRIVATE_KEY_PATH"]).read_text()
    return _PRIVATE_KEY


def _jwt_audience() -> str:
    return os.environ.get("JWT_AUDIENCE", "ratis")


def access_token_expire_minutes() -> int:
    return int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))


def _refresh_token_expire_days() -> int:
    return int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "30"))


def _google_client_id() -> str:
    return os.environ["GOOGLE_CLIENT_ID"]


def _apple_client_id() -> str:
    return os.environ.get("APPLE_CLIENT_ID", "")


class UpstreamServiceError(Exception):
    """Raised when a third-party identity provider is unreachable."""


class LinkConflictError(Exception):
    """Raised when an OAuth identity is already linked to another account."""


class LastIdentityError(Exception):
    """Raised when unlinking would leave an account with zero identities."""


class IdentityNotFoundError(Exception):
    """Raised when the requested provider is not linked to the account."""


class RegistrationsClosedError(Exception):
    """Raised when account creation is disabled by the registration kill-switch.

    Only the OAuth *create* path is gated — existing identities still
    resolve and log in. Mapped to HTTP 503 ``registrations_closed`` by the
    ``/oauth`` route.
    """


# ============================================================
# JWT helpers
# ============================================================


def _create_token(
    user_id: uuid.UUID,
    token_type: str,
    expires_delta: timedelta,
    jti: str | None = None,
) -> str:
    expire = datetime.now(UTC) + expires_delta
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type,
        "exp": expire,
        "iat": datetime.now(UTC),
        "aud": _jwt_audience(),
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, _jwt_private_key(), algorithm="RS256")


def create_access_token(user_id: uuid.UUID) -> str:
    return _create_token(user_id, "access", timedelta(minutes=access_token_expire_minutes()))


def create_refresh_token(db: Session, user_id: uuid.UUID) -> str:
    """Issue a new refresh token and persist its JTI to the DB for revocation tracking."""
    jti = str(uuid.uuid4())
    expires_delta = timedelta(days=_refresh_token_expire_days())
    expires_at = datetime.now(UTC) + expires_delta
    token = _create_token(user_id, "refresh", expires_delta, jti=jti)
    refresh_token_repo.create(db, jti=jti, user_id=user_id, expires_at=expires_at)
    return token


# ============================================================
# Apple JWKS cache (thread-safe)
# ============================================================

_apple_jwks_cache: list[dict] = []
_apple_jwks_fetched_at: float = 0.0
_apple_jwks_lock = threading.Lock()
_APPLE_JWKS_TTL = 3600.0  # 1 hour


def _get_apple_jwks(force_refresh: bool = False) -> list[dict]:
    global _apple_jwks_cache, _apple_jwks_fetched_at
    now = time.monotonic()
    with _apple_jwks_lock:
        if not force_refresh and _apple_jwks_cache and (now - _apple_jwks_fetched_at) < _APPLE_JWKS_TTL:
            return _apple_jwks_cache
        response = httpx.get(APPLE_KEYS_URL, timeout=5)
        response.raise_for_status()
        _apple_jwks_cache = response.json()["keys"]
        _apple_jwks_fetched_at = now
        return _apple_jwks_cache


# ============================================================
# OAuth token verifiers (injectable for tests)
# ============================================================


def verify_google_token(token: str) -> dict[str, Any]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, google_requests.Request(), _google_client_id())


def verify_apple_token(token: str) -> dict[str, Any]:
    keys = _get_apple_jwks()
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    key = next((k for k in keys if k["kid"] == kid), None)
    if key is None:
        # Apple may have rotated keys — force-refresh cache and retry once
        keys = _get_apple_jwks(force_refresh=True)
        key = next((k for k in keys if k["kid"] == kid), None)
    if key is None:
        raise ValueError("Apple public key not found")

    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=_apple_client_id(),
        issuer="https://appleid.apple.com",
        options={"verify_at_hash": False},
    )
    return claims


def _apple_email_verified(claims: dict[str, Any]) -> bool:
    """Apple's ``email_verified`` claim is a string ('true'/'false') or a bool."""
    return str(claims.get("email_verified", "")).lower() == "true"


# ============================================================
# Service functions
# ============================================================


def _resolve_or_create_oauth_user(
    db: Session,
    *,
    provider: str,
    provider_id: str,
    email: str,
    display_name: str,
    avatar_url: str | None,
    timezone: str,
):
    """Resolve a user strictly by (provider, provider_id) identity.

    No match → create a brand-new account and its first identity. There is
    NO auto-link by email — cross-provider linking is explicit (see
    ``link_provider``). Two providers carrying the same email therefore
    produce two separate accounts, each storing that same real email;
    this is intentional (spec §4.2) and is exactly why ``users.email`` is
    no longer UNIQUE (see plan Decision 2).

    ``email`` may be empty when resolving an existing identity (Apple
    withholds it on re-login) — it is only required on the create path,
    enforced by the per-provider callers before this is reached.
    """
    identity = identity_repo.get_by_provider(db, provider, provider_id)
    if identity is not None:
        return user_repo.get_by_id(db, identity.user_id)

    # Registration kill-switch — only the create path is gated. An existing
    # identity (handled above) always logs in. Default fail-open (True) so
    # an unseeded ``app_settings`` never accidentally locks out signups.
    if not load_settings().get("auth", {}).get("registrations_open", True):
        raise RegistrationsClosedError("registrations_closed")

    user = user_repo.create_user(
        db,
        email=email,
        account_type="oauth",
        display_name=display_name,
        avatar_url=avatar_url,
        timezone=timezone,
    )
    identity_repo.create(db, user_id=user.id, provider=provider, provider_id=provider_id, email=email)
    prefs_repo.get_or_create(db, user.id)
    referral_repo.create_for_user(db, user.id)
    _create_cab_balance(db, user.id)
    _create_cashback_balance(db, user.id)
    return user


def oauth_google(db: Session, token: str, timezone: str = "Europe/Paris") -> tuple[str, str]:
    try:
        idinfo = verify_google_token(token)
    except ValueError:
        raise ValueError("invalid_google_token")
    except google_exc.TransportError as exc:
        raise UpstreamServiceError("google_auth_unavailable") from exc

    provider_id = idinfo["sub"]
    email = idinfo.get("email") or ""
    if not email:
        raise ValueError("google_missing_email")
    if not idinfo.get("email_verified"):
        raise ValueError("google_email_not_verified")

    resolved_name = (idinfo.get("name") or "").strip() or f"Ratis_{uuid.uuid4().hex[:4].upper()}"
    user = _resolve_or_create_oauth_user(
        db,
        provider="google",
        provider_id=provider_id,
        email=email,
        display_name=resolved_name,
        avatar_url=idinfo.get("picture"),
        timezone=timezone,
    )

    access = create_access_token(user.id)
    refresh = create_refresh_token(db, user.id)
    db.commit()
    return access, refresh


def oauth_apple(db: Session, token: str, timezone: str = "Europe/Paris") -> tuple[str, str]:
    # Apple sign-in is optional (Android-only builds leave APPLE_CLIENT_ID empty).
    # Without a configured audience the JWT verification cannot be trusted —
    # surface a clean 503 instead of a KeyError 500.
    if not os.environ.get("APPLE_CLIENT_ID"):
        raise UpstreamServiceError("apple_disabled")
    try:
        claims = verify_apple_token(token)
    except (ValueError, JWTError):
        raise ValueError("invalid_apple_token")
    except httpx.HTTPError as exc:
        raise UpstreamServiceError("apple_auth_unavailable") from exc

    provider_id = claims["sub"]
    email = claims.get("email") or ""
    if email and not _apple_email_verified(claims):
        raise ValueError("apple_email_not_verified")

    # Apple withholds email on re-login — the identity lookup needs no
    # email, but a brand-new account does. Resolve the existing identity
    # first; only require email when there is no account to create.
    existing = identity_repo.get_by_provider(db, "apple", provider_id)
    if existing is None and not email:
        raise ValueError("apple_missing_email")

    user = _resolve_or_create_oauth_user(
        db,
        provider="apple",
        provider_id=provider_id,
        email=email,
        display_name=f"Ratis_{uuid.uuid4().hex[:4].upper()}",
        avatar_url=None,
        timezone=timezone,
    )

    access = create_access_token(user.id)
    refresh = create_refresh_token(db, user.id)
    db.commit()
    return access, refresh


def list_identities(db: Session, user) -> list:
    """Return every OAuth identity linked to ``user``."""
    return identity_repo.list_for_user(db, user.id)


def _verify_oauth_identity(provider: str, token: str) -> tuple[str, str]:
    """Verify an OAuth token, return ``(provider_id, email)``.

    Mirrors the verification logic of ``oauth_google`` / ``oauth_apple`` but
    raises a generic ``ValueError("invalid_oauth_token")`` (opaque code) or
    ``UpstreamServiceError`` exactly like the login path.
    """
    if provider == "google":
        try:
            idinfo = verify_google_token(token)
        except ValueError:
            raise ValueError("invalid_oauth_token")
        except google_exc.TransportError as exc:
            raise UpstreamServiceError("google_auth_unavailable") from exc
        provider_id = idinfo["sub"]
        email = idinfo.get("email") or ""
        if not email or not idinfo.get("email_verified"):
            raise ValueError("invalid_oauth_token")
        return provider_id, email
    # apple
    if not os.environ.get("APPLE_CLIENT_ID"):
        raise UpstreamServiceError("apple_disabled")
    try:
        claims = verify_apple_token(token)
    except (ValueError, JWTError):
        raise ValueError("invalid_oauth_token")
    except httpx.HTTPError as exc:
        raise UpstreamServiceError("apple_auth_unavailable") from exc
    provider_id = claims["sub"]
    email = claims.get("email") or ""
    if email and not _apple_email_verified(claims):
        raise ValueError("invalid_oauth_token")
    return provider_id, email


def link_provider(db: Session, user, provider: str, token: str) -> None:
    """Attach the OAuth identity behind ``token`` to ``user``'s account.

    Raises ``LinkConflictError`` if the identity already belongs to a
    different account. Idempotent if it is already linked to ``user``.
    """
    provider_id, email = _verify_oauth_identity(provider, token)
    existing = identity_repo.get_by_provider(db, provider, provider_id)
    if existing is not None:
        if existing.user_id == user.id:
            return  # already linked to this account — idempotent no-op
        raise LinkConflictError("identity_already_linked")
    identity_repo.create(db, user_id=user.id, provider=provider, provider_id=provider_id, email=email)
    db.commit()


def unlink_provider(db: Session, user, provider: str) -> None:
    """Detach ``provider``'s identity from ``user``'s account.

    Raises ``IdentityNotFoundError`` if the provider is not linked, and
    ``LastIdentityError`` if it is the account's only identity (which would
    lock the user out — login resolves only via identities).
    """
    if identity_repo.get_by_provider_for_user(db, user.id, provider) is None:
        raise IdentityNotFoundError("identity_not_found")
    if identity_repo.count_for_user(db, user.id) <= 1:
        raise LastIdentityError("cannot_unlink_last_identity")
    identity_repo.delete_for_user(db, user.id, provider)
    db.commit()


def refresh_tokens(db: Session, token: str) -> tuple[str, str]:
    """Validate a refresh token, revoke it, and issue a new access+refresh pair."""
    try:
        jti = decode_refresh_token(token)
    except ValueError:
        raise PermissionError("invalid_refresh_token")

    db_token = refresh_token_repo.get_by_jti(db, jti)
    if not db_token or db_token.revoked_at is not None:
        raise PermissionError("invalid_refresh_token")
    if db_token.expires_at < datetime.now(UTC):
        raise PermissionError("invalid_refresh_token")

    user_id = db_token.user_id

    # Atomic: revoke old token, issue new pair
    refresh_token_repo.revoke(db, db_token)
    new_access = create_access_token(user_id)
    new_refresh = create_refresh_token(db, user_id)
    db.commit()

    return new_access, new_refresh
