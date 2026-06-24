"""Admin subscription manage endpoints.

Endpoints :

- ``GET    /admin/users/{user_id}/subscription`` — read-only state
  (``ADMIN_API_KEY`` only — no TOTP, read-only is not financially mutative)

- ``PATCH  /admin/users/{user_id}/subscription/activate`` — TOTP-gated
  Force activates a manual subscription (``paid_with='manual_admin'``) ;
  default expiry +1 year unless ``until_date`` provided. Idempotent : 409 if
  already active with future expiry.

- ``PATCH  /admin/users/{user_id}/subscription/deactivate`` — TOTP-gated
  ``effective='immediate'`` → status='cancelled' + cancelled_at=now() +
  expires_at=now() ; ``effective='end_of_period'`` → no row mutation, intent
  recorded via audit log only (Stripe webhook = source of truth at expiry).

- ``PATCH  /admin/users/{user_id}/subscription/extend`` — TOTP-gated
  Push expires_at forward (trial grace). Validates ``new_expires_at`` is
  strictly after the current expiry and after now().

Subscription = ``NEVER PURGE`` (legal). Mutations UPDATE in place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.models.rewards import Subscription
from ratis_core.totp_service import verify_totp_dep
from services.subscription_admin_service import (
    SubscriptionAlreadyActiveError,
    SubscriptionExtendInvalidError,
    SubscriptionNotFoundError,
    activate_subscription,
    deactivate_subscription,
    extend_subscription,
    get_subscription,
)
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_to_eod_dt(d: date) -> datetime:
    """Coerce a date payload to end-of-day UTC datetime — admin grants run
    until midnight, not 00:00 of the same day."""
    return datetime.combine(d, time(23, 59, 59), tzinfo=UTC)


def _serialize(sub: Subscription, *, extra: dict[str, Any] | None = None) -> dict:
    """Stable wire shape across all four endpoints."""
    body = {
        "id": str(sub.id),
        "user_id": str(sub.user_id),
        "status": sub.status,
        "plan": sub.plan,
        "paid_with": sub.paid_with,
        "payment_ref": sub.payment_ref,
        "started_at": sub.started_at.isoformat() if sub.started_at else None,
        "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
    }
    if extra:
        body.update(extra)
    return body


# ---------------------------------------------------------------------------
# GET — read-only state (ADMIN_API_KEY only, no TOTP)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/users/{user_id}/subscription",
    status_code=200,
    dependencies=[Depends(verify_admin_key)],
)
def admin_get_subscription(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Return the most-recent subscription row for ``user_id``.

    Errors :
    - 403 ``forbidden`` — wrong/missing ADMIN_API_KEY (verify_admin_key)
    - 404 ``subscription_not_found`` — no row exists for the user
    """
    try:
        sub = get_subscription(db, user_id)
    except SubscriptionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="subscription_not_found",
        )
    return _serialize(sub)


# ---------------------------------------------------------------------------
# PATCH activate — TOTP-gated
# ---------------------------------------------------------------------------


class ActivateRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    until_date: date | None = None
    source: Literal["manual_admin"] = "manual_admin"


@router.patch(
    "/admin/users/{user_id}/subscription/activate",
    status_code=200,
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def admin_activate_subscription(
    user_id: uuid.UUID,
    body: ActivateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict:
    """Force-activate a subscription (manual grant, alpha/promo, support).

    Body :
        reason : str (≥3 chars, audit trail)
        until_date : date (optional ; default = today + 365 days)
        source : 'manual_admin' (literal — extension point for future sources)

    Errors :
    - 401 ``totp_required`` / ``totp_invalid`` — 2FA failure
    - 403 ``forbidden`` — wrong ADMIN_API_KEY
    - 409 ``already_active`` — subscription already active with future expiry
    """
    until_dt = _date_to_eod_dt(body.until_date) if body.until_date else None
    try:
        sub = activate_subscription(
            db,
            user_id,
            reason=body.reason,
            until_date=until_dt,
            operator=x_admin_operator,
        )
    except SubscriptionAlreadyActiveError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="already_active",
        )
    db.commit()
    return _serialize(sub)


# ---------------------------------------------------------------------------
# PATCH deactivate — TOTP-gated
# ---------------------------------------------------------------------------


class DeactivateRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    effective: Literal["immediate", "end_of_period"]


@router.patch(
    "/admin/users/{user_id}/subscription/deactivate",
    status_code=200,
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def admin_deactivate_subscription(
    user_id: uuid.UUID,
    body: DeactivateRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict:
    """Cancel a subscription.

    - ``effective='immediate'`` → status='cancelled' + cancelled_at=now() +
      expires_at=now().
    - ``effective='end_of_period'`` → no DB mutation. Intent recorded in
      audit log ; Stripe webhook flips to 'cancelled' at natural expiry.

    Subscription rows are NEVER deleted (legal — every paid month is a
    taxable event).

    Errors :
    - 401 ``totp_required`` / ``totp_invalid`` — 2FA failure
    - 403 ``forbidden`` — wrong ADMIN_API_KEY
    - 404 ``subscription_not_found`` — no row exists for the user
    """
    try:
        sub = deactivate_subscription(
            db,
            user_id,
            reason=body.reason,
            effective=body.effective,
            operator=x_admin_operator,
        )
    except SubscriptionNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="subscription_not_found",
        )
    db.commit()
    return _serialize(sub, extra={"effective": body.effective})


# ---------------------------------------------------------------------------
# PATCH extend — TOTP-gated
# ---------------------------------------------------------------------------


class ExtendRequest(BaseModel):
    new_expires_at: date
    reason: str = Field(min_length=3, max_length=200)


@router.patch(
    "/admin/users/{user_id}/subscription/extend",
    status_code=200,
    dependencies=[Depends(verify_admin_key), Depends(verify_totp_dep)],
)
def admin_extend_subscription(
    user_id: uuid.UUID,
    body: ExtendRequest,
    x_admin_operator: str = Header(alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict:
    """Push ``expires_at`` forward (trial grace).

    Validates ``new_expires_at`` is strictly after the current expires_at
    AND after now(). Sub must exist.

    Errors :
    - 401 ``totp_required`` / ``totp_invalid`` — 2FA failure
    - 403 ``forbidden`` — wrong ADMIN_API_KEY
    - 404 ``subscription_not_found`` — no row exists for the user
    - 422 ``invalid_extension_date`` — new_expires_at not strictly after current
    """
    new_expires_dt = _date_to_eod_dt(body.new_expires_at)
    try:
        sub = extend_subscription(
            db,
            user_id,
            new_expires_at=new_expires_dt,
            reason=body.reason,
            operator=x_admin_operator,
        )
    except SubscriptionNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="subscription_not_found",
        )
    except SubscriptionExtendInvalidError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid_extension_date",
        )
    db.commit()
    return _serialize(sub)
