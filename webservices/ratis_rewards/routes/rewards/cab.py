"""
GET /rewards/cab/balance — user-facing, JWT auth required.
"""

from __future__ import annotations

from deps import get_current_user
from fastapi import APIRouter, Depends
from ratis_core.database import get_db
from ratis_core.models.user import User
from services.cab_service import get_balance_with_battlepass
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/rewards/cab/balance")
def get_cab_balance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the authenticated user's CAB balance and battlepass progress."""
    return get_balance_with_battlepass(db, current_user.id)
