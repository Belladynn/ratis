from typing import Literal

import services.account_service as account_service
import services.auth_service as auth_service
from fastapi import APIRouter, Depends, HTTPException, Request, status
from limiter import limiter
from pydantic import BaseModel
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from ratis_core.schemas import (
    IdentityResponse,
    UserPreferencesResponse,
    UserPreferencesUpdate,
    UserResponse,
    UserUpdate,
)
from services.rings_service import claim_ring
from services.stats_service import compute_account_stats
from sqlalchemy.orm import Session

router = APIRouter()


class LogoutRequest(BaseModel):
    refresh_token: str


# ============================================================
# PROFILE
# ============================================================


@router.get("/profile", response_model=UserResponse)
def get_profile(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    return account_service.get_profile(user)


@router.patch("/profile", response_model=UserResponse)
def patch_profile(data: UserUpdate, token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    return account_service.update_profile(db, user, data)


# ============================================================
# STATS
# ============================================================


@router.get("/stats")
def get_stats(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    """Return aggregated scan/saving stats for the Profil screen."""
    user = get_http_current_user(db, token)
    return compute_account_stats(db, user)


# ============================================================
# ROI RINGS — claim one pending ring (atomic)
# ============================================================


@router.post("/rings/claim")
def post_rings_claim(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    """Break one pending ROI ring, if any.

    Response shape :
        {
            "animation": "claimed" | "nothing_to_claim",
            "rings_consumed": int,
            "pending_rings": int,
            "subscription_price_cents": int
        }
    """
    user = get_http_current_user(db, token)
    return claim_ring(db, user)


# ============================================================
# PREFERENCES
# ============================================================


@router.get("/preferences", response_model=UserPreferencesResponse)
def get_preferences(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    return account_service.get_preferences(db, user)


@router.patch("/preferences", response_model=UserPreferencesResponse)
def patch_preferences(
    data: UserPreferencesUpdate,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return account_service.update_preferences(db, user, data.search_radius_km, data.transport_mode)


# ============================================================
# LINKED OAUTH IDENTITIES
# ============================================================


@router.get("/identities", response_model=list[IdentityResponse])
def get_identities(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    """List the OAuth identities linked to the current account."""
    user = get_http_current_user(db, token)
    return auth_service.list_identities(db, user)


class LinkProviderRequest(BaseModel):
    provider: Literal["google", "apple"]
    token: str


@router.post("/link-provider", status_code=status.HTTP_200_OK)
def link_provider(
    data: LinkProviderRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Link an additional OAuth identity to the current account."""
    user = get_http_current_user(db, token)
    try:
        auth_service.link_provider(db, user, data.provider, data.token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_oauth_token")
    except auth_service.UpstreamServiceError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream_service_error")
    except auth_service.LinkConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="identity_already_linked")
    return {"detail": "provider_linked"}


@router.delete("/identities/{provider}", status_code=status.HTTP_200_OK)
def delete_identity(
    provider: Literal["google", "apple"],
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Unlink an OAuth identity from the current account."""
    user = get_http_current_user(db, token)
    try:
        auth_service.unlink_provider(db, user, provider)
    except auth_service.IdentityNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="identity_not_found")
    except auth_service.LastIdentityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot_unlink_last_identity")
    return {"detail": "provider_unlinked"}


# ============================================================
# SESSION MANAGEMENT
# ============================================================


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(data: LogoutRequest, token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    account_service.logout(db, user, data.refresh_token)
    return {"detail": "logged_out"}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
def logout_all(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    account_service.logout_all(db, user)
    return {"detail": "all_sessions_revoked"}


# ============================================================
# ACCOUNT DELETION (RGPD)
# ============================================================


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("1/hour")
def delete_account(request: Request, token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    account_service.delete_account(db, user)
