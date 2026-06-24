import logging
from typing import Literal

import services.subscription_service as sub_service
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from ratis_core.schemas import SubscriptionResponse
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
router = APIRouter()


_ALLOWED_CURRENCIES = {
    "eur",
    "usd",
    "gbp",
    "chf",
    "cad",
    "aud",
    "jpy",
    "nok",
    "sek",
    "dkk",
    "pln",
    "czk",
    "huf",
    "ron",
    "bgn",
    "hrk",
    "ils",
    "mxn",
    "brl",
    "sgd",
    "hkd",
    "nzd",
    "zar",
    "myr",
    "thb",
    "inr",
}


class SubscribeRequest(BaseModel):
    plan: Literal["monthly", "annual"]
    discount_campaign_code: str | None = None
    currency: str = "eur"

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        v = v.lower()
        if v not in _ALLOWED_CURRENCIES:
            raise ValueError("invalid_currency")
        return v


class CheckoutResponse(BaseModel):
    checkout_url: str


@router.post("", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
def subscribe(
    data: SubscribeRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        url = sub_service.create_checkout(
            db,
            user_id=user.id,
            plan=data.plan,
            discount_campaign_code=data.discount_campaign_code,
            currency=data.currency,
        )
    except ValueError as exc:
        code = str(exc)
        if code not in {
            "discount_code_invalid",
            "discount_code_expired",
            "discount_code_exhausted",
            "already_subscribed",
            "invalid_catalog_price",
        }:
            code = "subscription_error"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=code)
    except Exception:
        log.exception("Stripe checkout creation failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream_service_error")
    return CheckoutResponse(checkout_url=url)


@router.get("", response_model=SubscriptionResponse)
def get_subscription(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    sub = sub_service.get_active(db, user.id)
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_active_subscription")
    return sub


@router.delete("", status_code=status.HTTP_200_OK)
def cancel_subscription(token: str = Depends(get_bearer_token), db: Session = Depends(get_db)):
    user = get_http_current_user(db, token)
    try:
        sub_service.cancel_active(db, user.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception:
        log.exception("Stripe cancellation failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream_service_error")
    return {"detail": "subscription_cancelled"}
