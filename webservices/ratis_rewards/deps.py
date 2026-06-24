"""Shared FastAPI dependencies for ratis_rewards."""

from __future__ import annotations

from fastapi import Depends
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from ratis_core.models.user import User
from sqlalchemy.orm import Session


def get_current_user(
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
) -> User:
    return get_http_current_user(db, token)
