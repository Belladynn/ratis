"""Shopping list business logic."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ratis_core.models.product import Product
from ratis_core.models.shopping import ShoppingList, ShoppingListItem
from ratis_core.settings import load_settings
from ratis_core.utils import assert_owner
from repositories import shopping_list_repository as repo
from sqlalchemy.orm import Session


class ListNotFound(Exception):
    pass


class ItemNotFound(Exception):
    pass


class ProductNotFound(Exception):
    pass


class ItemAlreadyInList(Exception):
    pass


class ListFull(Exception):
    pass


class QuantityTooHigh(Exception):
    pass


class MaxTemplatesReached(Exception):
    pass


class NotATemplate(Exception):
    pass


class AlreadyATemplate(Exception):
    pass


@dataclass
class ScanCheckResult:
    status: str  # "checked" | "already_checked" | "not_in_list"
    item: ShoppingListItem | None = None
    product: Product | None = None


def _touch_list(sl: ShoppingList) -> None:
    """Bump the parent list's ``updated_at`` after a content mutation.

    The PG trigger ``trg_shopping_lists_updated_at`` only fires on an UPDATE
    of the ``shopping_lists`` row itself — adding, removing or checking items
    touches only ``shopping_list_items``, so the parent timestamp would go
    stale. Bumping it explicitly keeps "last modified" ordering correct.
    """
    sl.updated_at = datetime.now(UTC)


def _validate_quantity(quantity: float) -> None:
    """Reject a per-item quantity above the configured cap (R19).

    Raises:
        QuantityTooHigh: quantity exceeds list_optimiser.max_quantity_per_item.
    """
    cfg = load_settings().get("list_optimiser", {})
    max_quantity = cfg["max_quantity_per_item"]
    if quantity > max_quantity:
        raise QuantityTooHigh()


def create_shopping_list(db: Session, user_id: uuid.UUID, name: str | None = None) -> ShoppingList:
    return repo.create_list(db, user_id, name)


def get_user_lists(db: Session, user_id: uuid.UUID) -> list[dict]:
    return repo.get_lists_by_user(db, user_id)


def get_shopping_list(db: Session, list_id: uuid.UUID, user_id: uuid.UUID) -> ShoppingList:
    sl = repo.get_list_with_items(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)
    return sl


def update_shopping_list(db: Session, list_id: uuid.UUID, user_id: uuid.UUID, name: str) -> ShoppingList:
    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)
    return repo.update_list(db, sl, name)


def delete_shopping_list(db: Session, list_id: uuid.UUID, user_id: uuid.UUID) -> None:
    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)
    repo.delete_list(db, sl)


def add_item_to_list(
    db: Session,
    list_id: uuid.UUID,
    user_id: uuid.UUID,
    product_ean: str,
    quantity: float = 1,
) -> ShoppingListItem:
    _validate_quantity(quantity)

    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    product = db.get(Product, product_ean)
    if product is None:
        raise ProductNotFound()

    if repo.item_exists(db, list_id, product_ean):
        raise ItemAlreadyInList()

    cfg = load_settings().get("list_optimiser", {})
    max_items = cfg["max_items_per_list"]
    if repo.count_items(db, list_id) >= max_items:
        raise ListFull()

    item = repo.add_item(db, list_id, product_ean, quantity)
    _touch_list(sl)
    db.refresh(item, ["product"])
    return item


def update_item(
    db: Session,
    list_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    quantity: float | None = None,
    checked: bool | None = None,
) -> ShoppingListItem:
    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    item = repo.get_item(db, item_id)
    if item is None or item.list_id != list_id:
        raise ItemNotFound()

    if quantity is not None:
        _validate_quantity(quantity)
        item.quantity = quantity
    if checked is not None:
        item.checked = checked
        item.checked_at = datetime.now(UTC) if checked else None

    _touch_list(sl)
    db.flush()
    return item


def delete_item(
    db: Session,
    list_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    item = repo.get_item(db, item_id)
    if item is None or item.list_id != list_id:
        raise ItemNotFound()

    repo.delete_item(db, item)
    _touch_list(sl)


# -- Templates -------------------------------------------------------------


def save_as_template(db: Session, list_id: uuid.UUID, user_id: uuid.UUID) -> ShoppingList:
    """Copy a list as a template (is_template=true). Max 3 per user.

    Raises:
        AlreadyATemplate: the source list is itself a template.
    """
    sl = repo.get_list_with_items(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    if sl.is_template:
        raise AlreadyATemplate()

    cfg = load_settings().get("list_optimiser", {})
    max_templates = cfg["max_templates_per_user"]
    if repo.count_templates_by_user(db, user_id) >= max_templates:
        raise MaxTemplatesReached()

    # Create a copy as template
    tpl = ShoppingList(
        user_id=user_id,
        name=sl.name,
        has_default_name=sl.has_default_name,
        is_template=True,
    )
    db.add(tpl)
    db.flush()

    # Copy items
    for item in sl.items:
        new_item = ShoppingListItem(
            list_id=tpl.id,
            product_ean=item.product_ean,
            quantity=item.quantity,
        )
        db.add(new_item)
    db.flush()

    return tpl


def create_from_template(
    db: Session,
    template_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str | None = None,
) -> ShoppingList:
    """Create a new regular list by copying items from a template."""
    tpl = repo.get_list_with_items(db, template_id)
    if tpl is None:
        raise ListNotFound()
    assert_owner(tpl, user_id)

    if not tpl.is_template:
        raise NotATemplate()

    new_list = ShoppingList(user_id=user_id)
    if name:
        new_list.name = name
        new_list.has_default_name = False
    db.add(new_list)
    db.flush()

    for item in tpl.items:
        new_item = ShoppingListItem(
            list_id=new_list.id,
            product_ean=item.product_ean,
            quantity=item.quantity,
        )
        db.add(new_item)
    db.flush()

    return new_list


# -- Clear list -------------------------------------------------------------


def clear_list(db: Session, list_id: uuid.UUID, user_id: uuid.UUID) -> ShoppingList:
    """Remove all items from a list. Returns the empty list."""
    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    repo.clear_items(db, list_id)
    _touch_list(sl)
    return sl


# -- Scan check -------------------------------------------------------------


def scan_check(
    db: Session,
    list_id: uuid.UUID,
    user_id: uuid.UUID,
    product_ean: str,
) -> ScanCheckResult:
    """
    Check a product in the list by barcode scan.

    Returns:
        ScanCheckResult with status:
        - "checked" — item was in the list and just got checked
        - "already_checked" — item was already checked
        - "not_in_list" — item not in list; product info returned if known
    """
    sl = repo.get_list(db, list_id)
    if sl is None:
        raise ListNotFound()
    assert_owner(sl, user_id)

    item = repo.find_item_by_ean(db, list_id, product_ean)

    if item is not None:
        if item.checked:
            return ScanCheckResult(status="already_checked", item=item)
        # Check it
        item.checked = True
        item.checked_at = datetime.now(UTC)
        _touch_list(sl)
        db.flush()
        return ScanCheckResult(status="checked", item=item)

    # Not in list — look up product for suggestion
    product = db.get(Product, product_ean)
    return ScanCheckResult(status="not_in_list", product=product)
