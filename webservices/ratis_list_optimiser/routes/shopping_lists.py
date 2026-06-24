"""Shopping list and item routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from services import shopping_list_service as svc
from services.category_mapper import resolve_category as _resolve_category
from sqlalchemy.orm import Session

router = APIRouter(prefix="/lists", tags=["shopping-lists"])


# -- Schemas ---------------------------------------------------------------

_EAN_PATTERN = r"^\d{8,14}$"


class CreateListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)


class UpdateListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)


class AddItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ean: str = Field(max_length=20, pattern=_EAN_PATTERN)
    quantity: float = Field(default=1, gt=0)


class UpdateItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: float | None = Field(default=None, gt=0)
    checked: bool | None = None


class FromTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)


class ScanCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ean: str = Field(max_length=20, pattern=_EAN_PATTERN)


# -- Helpers ---------------------------------------------------------------


def _resolve_display_name(sl) -> str | None:
    """Return the list's user-facing name, or None when it uses a default name."""
    return sl.name if not sl.has_default_name else None


def _format_list_summary(row: dict) -> dict:
    sl = row["list"]
    name = _resolve_display_name(sl)
    return {
        "id": sl.id,
        "name": name,
        "has_default_name": sl.has_default_name,
        "is_template": sl.is_template,
        "item_count": row["item_count"],
        "unchecked_count": row["unchecked_count"],
        "created_at": sl.created_at.isoformat(),
        "updated_at": sl.updated_at.isoformat(),
    }


def _format_item(item) -> dict:
    return {
        "id": item.id,
        "product_ean": item.product_ean,
        "product_name": item.product.name if item.product else item.product_ean,
        "quantity": float(item.quantity),
        "checked": item.checked,
        "checked_at": item.checked_at.isoformat() if item.checked_at else None,
        # Wave 12 — derived category surfaced so the FE can group rows
        # under section headers (« Frais », « Boulangerie », « Épicerie »,
        # « Boissons », « Vrac », « Autres »). Resolved server-side from
        # ``products.storage_type`` + ``products.categories_tags`` keyword
        # match, so the FE never has to keep its own heuristic in sync
        # with the OFF taxonomy. See ``services/category_mapper.py``.
        "category": _resolve_category(item.product) if item.product else None,
    }


def _format_list_detail(sl, items=None) -> dict:
    """Format a list detail response.

    ``items`` lets the caller pass an explicit item collection (e.g. ``[]``
    right after a clear) instead of triggering a relationship load on ``sl``.
    """
    if items is None:
        items = sl.items
    return {
        "id": sl.id,
        "name": _resolve_display_name(sl),
        "has_default_name": sl.has_default_name,
        "is_template": sl.is_template,
        "items": [_format_item(item) for item in items],
        "created_at": sl.created_at.isoformat(),
        "updated_at": sl.updated_at.isoformat(),
    }


# -- List routes -----------------------------------------------------------


@router.post("", status_code=201)
def create_list(
    body: CreateListRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    sl = svc.create_shopping_list(db, user.id, body.name)
    db.commit()
    db.refresh(sl)
    name = _resolve_display_name(sl)
    return {
        "id": sl.id,
        "name": name,
        "has_default_name": sl.has_default_name,
        "items": [],
        "created_at": sl.created_at.isoformat(),
    }


@router.get("")
def get_lists(
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    rows = svc.get_user_lists(db, user.id)
    return [_format_list_summary(r) for r in rows]


@router.get("/{list_id}")
def get_list(
    list_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        sl = svc.get_shopping_list(db, list_id, user.id)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    # assert_owner raises HTTPException(403) directly
    return _format_list_detail(sl)


@router.patch("/{list_id}")
def update_list(
    list_id: uuid.UUID,
    body: UpdateListRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        sl = svc.update_shopping_list(db, list_id, user.id, body.name)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    db.commit()
    db.refresh(sl)
    name = _resolve_display_name(sl)
    return {
        "id": sl.id,
        "name": name,
        "has_default_name": sl.has_default_name,
        "created_at": sl.created_at.isoformat(),
        "updated_at": sl.updated_at.isoformat(),
    }


@router.delete("/{list_id}", status_code=204)
def delete_list(
    list_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        svc.delete_shopping_list(db, list_id, user.id)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    db.commit()


# -- Item routes -----------------------------------------------------------


@router.post("/{list_id}/items", status_code=201)
def add_item(
    list_id: uuid.UUID,
    body: AddItemRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        item = svc.add_item_to_list(db, list_id, user.id, body.product_ean, body.quantity)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    except svc.ProductNotFound:
        raise HTTPException(status_code=404, detail="product_not_found")
    except svc.ItemAlreadyInList:
        raise HTTPException(status_code=409, detail="item_already_in_list")
    except svc.ListFull:
        raise HTTPException(status_code=422, detail="list_full")
    except svc.QuantityTooHigh:
        raise HTTPException(status_code=422, detail="quantity_too_high")
    db.commit()
    db.refresh(item)
    return _format_item(item)


@router.patch("/{list_id}/items/{item_id}")
def update_item(
    list_id: uuid.UUID,
    item_id: uuid.UUID,
    body: UpdateItemRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        item = svc.update_item(
            db,
            list_id,
            item_id,
            user.id,
            quantity=body.quantity if "quantity" in body.model_fields_set else None,
            checked=body.checked if "checked" in body.model_fields_set else None,
        )
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    except svc.ItemNotFound:
        raise HTTPException(status_code=404, detail="item_not_found")
    except svc.QuantityTooHigh:
        raise HTTPException(status_code=422, detail="quantity_too_high")
    db.commit()
    db.refresh(item)
    return _format_item(item)


@router.delete("/{list_id}/items/{item_id}", status_code=204)
def delete_item(
    list_id: uuid.UUID,
    item_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    try:
        svc.delete_item(db, list_id, item_id, user.id)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    except svc.ItemNotFound:
        raise HTTPException(status_code=404, detail="item_not_found")
    db.commit()


# -- Template routes -------------------------------------------------------


@router.post("/{list_id}/save-as-template", status_code=201)
def save_as_template(
    list_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Save a copy of a list as a template."""
    user = get_http_current_user(db, token)
    try:
        tpl = svc.save_as_template(db, list_id, user.id)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    except svc.AlreadyATemplate:
        raise HTTPException(status_code=422, detail="already_a_template")
    except svc.MaxTemplatesReached:
        raise HTTPException(status_code=422, detail="max_templates_reached")
    db.commit()
    db.refresh(tpl, ["items"])
    for item in tpl.items:
        db.refresh(item, ["product"])
    return _format_list_detail(tpl)


@router.post("/from-template/{template_id}", status_code=201)
def create_from_template(
    template_id: uuid.UUID,
    body: FromTemplateRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Create a new list from a template."""
    user = get_http_current_user(db, token)
    try:
        sl = svc.create_from_template(db, template_id, user.id, body.name)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    except svc.NotATemplate:
        raise HTTPException(status_code=422, detail="not_a_template")
    db.commit()
    db.refresh(sl, ["items"])
    for item in sl.items:
        db.refresh(item, ["product"])
    return _format_list_detail(sl)


# -- Clear list -------------------------------------------------------------


@router.post("/{list_id}/clear")
def clear_list(
    list_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Remove all items from a list."""
    user = get_http_current_user(db, token)
    try:
        sl = svc.clear_list(db, list_id, user.id)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    db.commit()
    # Items were just cleared — format directly with an empty collection
    # instead of a superfluous full reload.
    return _format_list_detail(sl, items=[])


# -- Scan check -------------------------------------------------------------


@router.post("/{list_id}/scan-check")
def scan_check(
    list_id: uuid.UUID,
    body: ScanCheckRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Auto-check an item by scanning its barcode."""
    user = get_http_current_user(db, token)
    try:
        result = svc.scan_check(db, list_id, user.id, body.product_ean)
    except svc.ListNotFound:
        raise HTTPException(status_code=404, detail="list_not_found")
    db.commit()

    resp: dict = {"status": result.status}

    if result.item is not None:
        db.refresh(result.item, ["product"])
        resp["item"] = _format_item(result.item)
    elif result.status == "not_in_list":
        if result.product is not None:
            resp["product"] = {
                "ean": result.product.ean,
                "name": result.product.name,
            }
        else:
            resp["product"] = None

    return resp
