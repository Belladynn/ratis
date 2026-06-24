"""Cross-service shared schemas.

Reserved for Pydantic models that are imported by **multiple** services or that
encapsulate cross-cutting validation (e.g. ``check_timezone``). Service-local
request/response models should live next to their consuming route in
``webservices/<service>/`` — see ``ARCH_CORE.md`` § "Schema location convention".
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from ratis_core.utils import strip_str


def check_timezone(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError("invalid_timezone")
    return v


# Alias for internal use within this module
_check_timezone = check_timezone


# ============================================================
# BASE CONFIG
# Shared config for all response schemas that read from ORM objects.
# ============================================================
class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ============================================================
# MIXINS
# ============================================================


class _DisplayNameMixin(BaseModel):
    @field_validator("display_name", mode="before", check_fields=False)
    @classmethod
    def _strip_display_name(cls, v: object) -> object:
        return strip_str(v)


# ============================================================
# USERS
# ============================================================
class UserUpdate(_DisplayNameMixin):
    # display_name: non-nullable once set — None means "not provided" (no update)
    display_name: str | None = Field(default=None, min_length=1, max_length=30)
    # avatar_url: nullable — use model_fields_set to distinguish "not sent" from explicit null
    avatar_url: str | None = Field(default=None, max_length=2048, pattern=r"^https?://.+")
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str | None) -> str | None:
        return _check_timezone(v) if v is not None else None


class UserResponse(ORMModel):
    id: uuid.UUID
    email: str
    # Public, non-PII identifier of shape ``RTS-XXXXXX`` — surfaced so
    # the mobile app can render it on the profile screen for support.
    support_id: str
    account_type: str
    display_name: str | None
    avatar_url: str | None
    timezone: str
    current_level_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class IdentityResponse(ORMModel):
    """One OAuth identity linked to an account (for the "Comptes liés" UI)."""

    provider: str
    email: str | None
    created_at: datetime


# ============================================================
# AUTH
# ============================================================
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth token type, not a secret
    expires_in: int  # seconds until access token expiry — client should refresh 2 min before

    @computed_field  # type: ignore[misc]
    @property
    def expires_at(self) -> datetime:
        """Absolute UTC expiry timestamp — convenience for clients that prefer absolute time."""
        return datetime.now(UTC) + timedelta(seconds=self.expires_in)


# ============================================================
# PRODUCTS
# ============================================================
class ProductDetailResponse(ORMModel):
    """Rich product view returned by scan and product endpoints.

    Grows as OFF data and internal enrichment expand (allergens, labels, etc.).
    ``brand`` is mapped from the ORM column ``brands`` via validation_alias.
    """

    ean: str
    name: str
    brand: str | None = Field(None, validation_alias="brands")
    photo_url: str | None = None
    storage_type: str | None = None
    product_quantity: float | None = None
    product_quantity_unit: str | None = None


# ============================================================
# USER PREFERENCES
# ============================================================
class UserPreferencesUpdate(BaseModel):
    search_radius_km: int | None = Field(default=None, gt=0, le=50)
    transport_mode: Literal["driving", "walking", "cycling"] | None = None


class UserPreferencesResponse(ORMModel):
    user_id: uuid.UUID
    search_radius_km: int
    transport_mode: Literal["driving", "walking", "cycling"]
    created_at: datetime
    updated_at: datetime


# ============================================================
# SUBSCRIPTIONS
# ============================================================
class SubscriptionResponse(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    price: Decimal
    paid_with: str
    discount_campaign_code: str | None
    discount_amount: Decimal | None
    started_at: datetime
    expires_at: datetime
    cancelled_at: datetime | None
