"""
GET /gamification/xp/balance — user's XP balance and current level.
"""

from __future__ import annotations

from deps import get_current_user
from fastapi import APIRouter, Depends
from ratis_core.database import get_db
from ratis_core.models.user import User
from repositories.xp_repository import get_xp_balance
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/gamification/xp/balance")
def get_xp_balance_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the authenticated user's XP balance and current level.

    Balance is returned as a string to avoid JSON float precision loss
    for astronomically large XP values (e.g. 10 * 2^200 via Stonks).
    """
    result = get_xp_balance(db, current_user.id)
    return {
        "balance": str(result["balance"]),
        "level": result["level"],
    }
