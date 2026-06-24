"""CRUD operations for shopping_lists and shopping_list_items."""

from __future__ import annotations

import uuid

from ratis_core.models.shopping import ShoppingList, ShoppingListItem
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload


def create_list(db: Session, user_id: uuid.UUID, name: str | None = None) -> ShoppingList:
    sl = ShoppingList(user_id=user_id)
    if name:
        sl.name = name
        sl.has_default_name = False
    db.add(sl)
    db.flush()
    return sl


def get_lists_by_user(db: Session, user_id: uuid.UUID) -> list[dict]:
    """Return user's lists with item_count and unchecked_count."""
    stmt = (
        select(
            ShoppingList,
            func.count(ShoppingListItem.id).label("item_count"),
            func.count(ShoppingListItem.id)
            .filter(ShoppingListItem.checked == False)  # noqa: E712
            .label("unchecked_count"),
        )
        .outerjoin(ShoppingListItem, ShoppingList.id == ShoppingListItem.list_id)
        .where(ShoppingList.user_id == user_id)
        .group_by(ShoppingList.id)
        .order_by(ShoppingList.created_at.desc())
    )
    rows = db.execute(stmt).all()
    return [
        {
            "list": row[0],
            "item_count": row[1],
            "unchecked_count": row[2],
        }
        for row in rows
    ]


def get_list(db: Session, list_id: uuid.UUID) -> ShoppingList | None:
    return db.get(ShoppingList, list_id)


def get_list_with_items(db: Session, list_id: uuid.UUID) -> ShoppingList | None:
    stmt = (
        select(ShoppingList)
        .options(joinedload(ShoppingList.items).joinedload(ShoppingListItem.product))
        .where(ShoppingList.id == list_id)
    )
    return db.scalars(stmt).unique().first()


def update_list(db: Session, sl: ShoppingList, name: str) -> ShoppingList:
    sl.name = name
    sl.has_default_name = False
    db.flush()
    return sl


def delete_list(db: Session, sl: ShoppingList) -> None:
    db.delete(sl)
    db.flush()


def add_item(db: Session, list_id: uuid.UUID, product_ean: str, quantity: float = 1) -> ShoppingListItem:
    item = ShoppingListItem(list_id=list_id, product_ean=product_ean, quantity=quantity)
    db.add(item)
    db.flush()
    return item


def get_item(db: Session, item_id: uuid.UUID) -> ShoppingListItem | None:
    return db.get(ShoppingListItem, item_id)


def item_exists(db: Session, list_id: uuid.UUID, product_ean: str) -> bool:
    stmt = select(ShoppingListItem.id).where(
        ShoppingListItem.list_id == list_id,
        ShoppingListItem.product_ean == product_ean,
    )
    return db.scalar(stmt) is not None


def count_items(db: Session, list_id: uuid.UUID) -> int:
    stmt = select(func.count(ShoppingListItem.id)).where(ShoppingListItem.list_id == list_id)
    return db.scalar(stmt) or 0


def delete_item(db: Session, item: ShoppingListItem) -> None:
    db.delete(item)
    db.flush()


def count_templates_by_user(db: Session, user_id: uuid.UUID) -> int:
    """Count how many template lists a user has."""
    stmt = select(func.count(ShoppingList.id)).where(
        ShoppingList.user_id == user_id,
        ShoppingList.is_template == True,  # noqa: E712
    )
    return db.scalar(stmt) or 0


def clear_items(db: Session, list_id: uuid.UUID) -> None:
    """Delete all items from a list."""
    stmt = select(ShoppingListItem).where(ShoppingListItem.list_id == list_id)
    items = db.scalars(stmt).all()
    for item in items:
        db.delete(item)
    db.flush()


def find_item_by_ean(db: Session, list_id: uuid.UUID, product_ean: str) -> ShoppingListItem | None:
    """Find an item in a list by its product EAN."""
    stmt = select(ShoppingListItem).where(
        ShoppingListItem.list_id == list_id,
        ShoppingListItem.product_ean == product_ean,
    )
    return db.scalars(stmt).first()
