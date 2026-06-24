from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from services import suggestion_service as svc
from sqlalchemy.orm import Session

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_id: uuid.UUID


@router.get("/eligibility")
def check_eligibility(
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return svc.check_eligibility(db, user.id)


@router.post("/generate")
def generate_suggestions(
    body: GenerateRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        suggestions, added = svc.generate_and_add_to_list(db, user.id, body.list_id)
    except svc.NotEligible:
        raise HTTPException(status_code=422, detail="not_eligible")
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    db.commit()
    return {
        "suggestions": [
            {
                "product_ean": s.product_ean,
                "product_name": s.product_name,
                "frequency": s.frequency,
                "appearances": s.appearances,
                "total_receipts": s.total_receipts,
            }
            for s in suggestions
        ],
        "added_to_list": added,
    }
