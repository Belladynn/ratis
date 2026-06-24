import logging
from typing import Literal

import services.auth_service as auth_service
from fastapi import APIRouter, Depends, HTTPException, Request, status
from limiter import limiter
from pydantic import BaseModel, field_validator
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from ratis_core.schemas import TokenResponse, UserResponse, check_timezone
from services.auth_service import RegistrationsClosedError, UpstreamServiceError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
router = APIRouter()


class OAuthRequest(BaseModel):
    provider: Literal["google", "apple"]
    token: str
    timezone: str = "Europe/Paris"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        return check_timezone(v)


class RefreshRequest(BaseModel):
    refresh_token: str


# ============================================================
# OAUTH (Google + Apple)
# ============================================================
@router.post("/oauth", response_model=TokenResponse)
@limiter.limit("5/minute")
def oauth(request: Request, data: OAuthRequest, db: Session = Depends(get_db)):
    log.info("oauth attempt provider=%s", data.provider)
    try:
        if data.provider == "google":
            access_token, refresh_token = auth_service.oauth_google(db, data.token, data.timezone)
        else:  # "apple" — enforced by Literal
            access_token, refresh_token = auth_service.oauth_apple(db, data.token, data.timezone)
    except RegistrationsClosedError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="registrations_closed")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_oauth_token")
    except UpstreamServiceError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream_service_error")
    expires_in = auth_service.access_token_expire_minutes() * 60
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=expires_in)


# ============================================================
# ME
# ============================================================
@router.get("/me", response_model=UserResponse)
def me(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    return get_http_current_user(db, token)


# ============================================================
# REFRESH
# ============================================================
@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("5/minute")
def refresh(request: Request, data: RefreshRequest, db: Session = Depends(get_db)):
    try:
        access_token, refresh_token = auth_service.refresh_tokens(db, data.refresh_token)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_refresh_token")
    expires_in = auth_service.access_token_expire_minutes() * 60
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=expires_in)
