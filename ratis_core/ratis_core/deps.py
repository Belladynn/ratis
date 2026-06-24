import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Three distinct instances — user JWT, inter-service, and admin flows must not share a bearer.
_user_bearer = HTTPBearer(auto_error=False)
_service_bearer = HTTPBearer(auto_error=False)
_admin_bearer = HTTPBearer(auto_error=False)


def get_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_user_bearer),
) -> str:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not_authenticated")
    return credentials.credentials


def verify_internal_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_service_bearer),
) -> None:
    """
    Validate the INTERNAL_API_KEY bearer token for inter-service endpoints.

    Always returns HTTP 403 on failure — never 500 — to avoid leaking internal
    configuration state to callers.

    INTERNAL_API_KEY must be set before the service starts. Use
    ratis_core.startup.require_env("INTERNAL_API_KEY") in the service lifespan
    to fail fast at startup rather than returning 403 on every request.
    """
    expected = os.environ.get("INTERNAL_API_KEY", "")
    # Short-circuit before compare_digest if either side is empty (avoids TypeError).
    # 403 even for missing config: startup require_env() is the right enforcement point.
    if not credentials or not expected or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


def verify_admin_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_admin_bearer),
) -> None:
    """
    Validate the ADMIN_API_KEY bearer token for admin endpoints.

    Always returns HTTP 403 on failure — never 500 — to avoid leaking internal
    configuration state to callers.

    ADMIN_API_KEY must be set before the service starts. Use
    ratis_core.startup.require_env("ADMIN_API_KEY") in the service lifespan
    to fail fast at startup rather than returning 403 on every request.
    """
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not credentials or not expected or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
