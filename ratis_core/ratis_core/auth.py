"""
Shared authentication helpers for all Ratis services.

Services import get_current_user from here — no HTTP call to ratis_auth.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ratis_core.jwt import decode_access_token
from ratis_core.models import User


def get_current_user(db: Session, token: str) -> User:
    """
    Resolve an access token to a User.

    Raises PermissionError for:
    - invalid / expired token
    - user not found
    - account marked as deleted (is_deleted=True)
    - token issued before password change (token_revoked)
    """
    try:
        user_id, token_iat = decode_access_token(token)
    except ValueError:
        raise PermissionError("invalid_token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise PermissionError("invalid_token")
    if user.is_deleted:
        raise PermissionError("account_deleted")
    if (
        user.password_changed_at is not None
        and token_iat is not None
        # JWT `iat` has second-level precision; password_changed_at is stored
        # truncated to the same precision (microsecond=0). Tokens issued in the
        # same second as the password change are accepted; strictly older ones
        # are rejected.
        and token_iat < user.password_changed_at
    ):
        raise PermissionError("token_revoked")
    return user


def get_http_current_user(db: Session, token: str) -> User:
    """
    Wrapper around get_current_user for FastAPI route handlers.
    Converts PermissionError to HTTPException(401) so routes stay clean.
    """
    try:
        return get_current_user(db, token)
    except PermissionError:
        raise HTTPException(status_code=401, detail="unauthorized")
