"""Business logic for product favorites."""

from __future__ import annotations

import uuid

from ratis_core.exceptions import NotFound
from repositories.barcode_repository import get_product
from repositories.favorites_repository import (
    add_favorite as repo_add,
)
from repositories.favorites_repository import (
    list_favorites as repo_list,
)
from repositories.favorites_repository import (
    remove_favorite as repo_remove,
)
from sqlalchemy.orm import Session


def add_product_favorite(db: Session, *, user_id: uuid.UUID, ean: str) -> dict:
    if get_product(db, ean) is None:
        raise NotFound("product_not_found")
    repo_add(db, user_id, ean)
    db.commit()
    return {"favorited": True}


def remove_product_favorite(db: Session, *, user_id: uuid.UUID, ean: str) -> dict:
    if get_product(db, ean) is None:
        raise NotFound("product_not_found")
    repo_remove(db, user_id, ean)
    db.commit()
    return {"favorited": False}


def list_user_favorites(db: Session, *, user_id: uuid.UUID, limit: int) -> dict:
    rows = repo_list(db, user_id, limit)
    return {
        "items": [
            {
                "ean": r["ean"],
                "name": r["name"],
                "photo_url_small": r["photo_url_small"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }
