from __future__ import annotations

import uuid
from typing import Protocol

from fastapi import HTTPException


class _OwnedResource(Protocol):
    user_id: uuid.UUID | None


def assert_owner(resource: _OwnedResource, user_id: uuid.UUID) -> None:
    """Raise HTTP 403 if resource.user_id does not match the authenticated user."""
    if resource.user_id != user_id:
        raise HTTPException(status_code=403, detail="forbidden")


def strip_str(v: object) -> object:
    """Strip leading/trailing whitespace from strings.

    Returns the stripped string (possibly empty) so that downstream validators
    such as min_length can still fire and return a proper 422.
    Pydantic-compatible: accepts any type and returns it unchanged if not a str.
    """
    if isinstance(v, str):
        return v.strip()
    return v
