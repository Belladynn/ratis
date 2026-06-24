"""DB access for product_favorites."""

from __future__ import annotations

import uuid

from ratis_core.models.product import Product, ProductFavorite
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session


def add_favorite(db: Session, user_id: uuid.UUID, product_ean: str) -> None:
    """Insert favorite, swallowing duplicates via ON CONFLICT DO NOTHING."""
    db.execute(
        pg_insert(ProductFavorite)
        .values(user_id=user_id, product_ean=product_ean)
        .on_conflict_do_nothing(index_elements=["user_id", "product_ean"])
    )
    db.flush()


def remove_favorite(db: Session, user_id: uuid.UUID, product_ean: str) -> None:
    """Delete favorite if present. No-op otherwise."""
    db.query(ProductFavorite).filter(
        ProductFavorite.user_id == user_id,
        ProductFavorite.product_ean == product_ean,
    ).delete(synchronize_session=False)
    db.flush()


def list_favorites(db: Session, user_id: uuid.UUID, limit: int) -> list[dict]:
    """Return the user's favorites, newest first, joined with product preview."""
    rows = (
        db.execute(
            select(
                Product.ean.label("ean"),
                Product.name.label("name"),
                Product.photo_url_small.label("photo_url_small"),
                ProductFavorite.created_at.label("created_at"),
            )
            .join(Product, Product.ean == ProductFavorite.product_ean)
            .where(ProductFavorite.user_id == user_id)
            .order_by(ProductFavorite.created_at.desc())
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
