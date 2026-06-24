from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from services.favorites_service import (
    add_product_favorite,
    list_user_favorites,
    remove_product_favorite,
)
from services.incomplete_service import list_incomplete_products
from services.product_contribute_service import (
    ContributionDailyCapExceeded,
    contribute_product_field,
)
from services.product_search_service import run_product_search
from services.product_service import get_product_detail
from services.suggestions_service import get_default_suggestions
from sqlalchemy.orm import Session

router = APIRouter()


# ── contribute (Phase C-5) ────────────────────────────────────────────────────


class ProductContributionRequest(BaseModel):
    """User-driven product field fill payload.

    ``value`` is a ``str`` for scalar fields (``brands`` / ``name``) and
    a ``list[str]`` for array fields (``categories_tags`` /
    ``labels_tags``). The mismatch case (string for an array field or
    vice-versa) is caught in the service layer and raises 422.
    """

    field: Literal["brands", "categories_tags", "labels_tags", "name"]
    value: str | list[str]


# ── search (declared BEFORE /{ean} to avoid path collision) ─────────────────


class ProductSearchHit(BaseModel):
    """Single product surfaced by the autocomplete search.

    Wave 9 enrichment (PO ticket 2026-05-13 « pomme de terre duplicate
    disambig ») surfaces ``quantity`` / ``labels_tags`` / ``origins_tags``
    so the FE can compose a secondary line (« Carrefour · 1 kg · 🇫🇷 »)
    that lets the user distinguish 8 identical-named hits without
    issuing a follow-up ``GET /product/{ean}``.

    ``quantity`` is the display string sourced from
    ``products.quantity_text`` (e.g. « 1 kg », « 6 x 33 cl », « 500 g
    sachet »). The internal column name doesn't bleed onto the wire —
    we expose the user-facing concept.
    """

    ean: str
    name: str
    brands: str | None = None
    quantity: str | None = None
    categories_tags: list[str] | None = None
    labels_tags: list[str] | None = None
    origins_tags: list[str] | None = None
    source: str


class ProductSearchResponse(BaseModel):
    items: list[ProductSearchHit]


class EnrichissementTaskResponse(BaseModel):
    """Single missing-field task surfaced by ``/product/incomplete``.

    Mirror of the ``EnrichissementTask`` TypeScript interface in
    ``ratis_client/types/gamification.ts`` — keep the field names in
    sync so the FE consumes the same shape from both the dashboard
    (``/incomplete?limit=1``, single task) and the « Compléter ce
    produit » screen (``/incomplete?limit=10``, batch).
    """

    product_ean: str
    product_name: str
    missing_field: Literal["name", "brands", "categories_tags", "labels_tags"]
    cab_reward: int


class IncompleteProductsResponse(BaseModel):
    items: list[EnrichissementTaskResponse]


DEFAULT_EMPTY_QUERY_LIMIT = 5


@router.get("/search", response_model=ProductSearchResponse)
def get_search(
    q: str = Query("", max_length=100),
    limit: int | None = Query(None, ge=1, le=50),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Search the product catalogue by name + brand.

    * Matches on ``products.name_normalized`` (unaccent + upper) plus
      ``products.brands_text`` (also unaccent + upper).
    * Excludes ``source='user_suggested'`` (pending admin validation).
    * Ordering : prefix match first, then shorter names first, then
      stable ean tiebreaker. See
      ``repositories/product_search_repository.py`` for the SQL.

    Wave 12 — empty ``q`` is now accepted (PO ticket 2026-05-14). When
    the FE focuses the AddBar with no typed query, the dropdown wants a
    default suggestion list. Empty ``q`` returns the top
    ``DEFAULT_EMPTY_QUERY_LIMIT`` (5) products sorted alphabetically.
    Typed ``q`` keeps the wave-9 enriched ranking + default limit 20.
    The caller may override ``limit`` in both modes (capped at 50).
    """
    # Auth is mandatory but we don't need the user object — just the
    # token validity check (consistent with the other product routes).
    get_http_current_user(db, token)
    is_empty = not q.strip()
    effective_limit = limit if limit is not None else (DEFAULT_EMPTY_QUERY_LIMIT if is_empty else 20)
    return run_product_search(db, query=q, limit=effective_limit)


# ── default suggestions (empty-state of the Liste/Produit search field) ─────


@router.get(
    "/suggestions/default",
    response_model=ProductSearchResponse,
    summary="Default empty-state suggestions for the search field",
)
def get_suggestions_default(
    limit: int = Query(5, ge=1, le=20),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Return a tier-composed list of product suggestions for the empty
    state of the Liste / Produit search field.

    Composition rule :
      1. Tier (c) : user's recent EANs (UNION matched scans + list items,
         deduped, recency-sorted)
      2. Tier (b) : curated FR staples, used to top up when (c) returns
         less than ``limit`` items

    Always emits up to ``limit`` rows (clamped to ``[1, 20]``).
    See ``docs/superpowers/specs/2026-05-14-default-search-3tier-design.md``.
    """
    user = get_http_current_user(db, token)
    items = get_default_suggestions(db, user.id, limit=limit)
    return ProductSearchResponse(items=items)


# ── incomplete products queue (declared BEFORE /{ean} to avoid path collision) ─


@router.get(
    "/incomplete",
    response_model=IncompleteProductsResponse,
    summary="List products with missing fields, ranked by popularity",
)
def get_incomplete(
    limit: int = Query(10, ge=1, le=50),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
) -> IncompleteProductsResponse:
    """Return up to ``limit`` products with at least one missing field
    (``brands_text``, ``categories_tags``, ``labels_tags``) ranked by
    cross-user popularity. Per product, pick ONE missing field to
    surface (priority ``brands > categories_tags > labels_tags``).

    Auth required (no per-user filtering : the same task list is shown
    to every user — popularity is computed cross-users). Reward is
    uniform across tasks, read from
    ``ratis_settings.rewards.cab_per_fill_product_field``.

    See ``docs/superpowers/specs/2026-05-14-completer-screen-design.md``.
    """
    # Auth check (token validity) — we don't use the user object since
    # popularity is cross-user, but auth is mandatory to keep the
    # endpoint behind the JWT wall.
    get_http_current_user(db, token)
    items = list_incomplete_products(db, limit=limit)
    return IncompleteProductsResponse(items=items)


# ── favorites (declared BEFORE /{ean} to avoid path collision) ───────────────


@router.get("/favorites")
def get_favorites(
    limit: int = Query(50, ge=1, le=100),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return list_user_favorites(db, user_id=user.id, limit=limit)


@router.post("/{ean}/favorite")
def post_favorite(
    ean: str = Path(..., pattern=r"^\d{8}$|^\d{13}$"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return add_product_favorite(db, user_id=user.id, ean=ean)


@router.delete("/{ean}/favorite")
def delete_favorite(
    ean: str = Path(..., pattern=r"^\d{8}$|^\d{13}$"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return remove_product_favorite(db, user_id=user.id, ean=ean)


@router.post("/{ean}/contribute")
def post_contribute(
    payload: ProductContributionRequest,
    response: Response,
    ean: str = Path(..., pattern=r"^\d{8}$|^\d{13}$"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Apply (or queue for admin review) a user contribution on a
    missing product field. Phase C-5 of the missions sprint. See
    ``services/product_contribute_service.py`` for the full contract.

    Response codes :
      * 201 — a new contribution row was created (status = applied or
              pending_review depending on whether the target field was
              empty).
      * 200 — idempotency window absorbed the call ; the existing row
              is returned, no new mission credit.
      * 401 — missing or invalid JWT.
      * 404 — unknown EAN.
      * 422 — validation failure (wrong value type, too long, bad tag
              shape, etc.).
      * 429 — per-user daily contribution cap reached (anti-spam).
    """
    user = get_http_current_user(db, token)
    try:
        result = contribute_product_field(
            db,
            user_id=user.id,
            ean=ean,
            field=payload.field,
            value=payload.value,
        )
    except ContributionDailyCapExceeded as exc:
        # No dedicated 429 domain exception in ratis_core.exceptions —
        # mirrors rescan_service.RescanCapExceeded handling (R12 :
        # HTTPException lives in the route layer only).
        raise HTTPException(
            status_code=429,
            detail=exc.detail,
        ) from exc
    # MANDATORY commit (R02) — service never commits ; route owns the
    # transaction boundary.
    db.commit()
    # 201 for fresh INSERTs ; 200 when the idempotency window kicked in.
    response.status_code = 200 if result.get("idempotent") else 201
    return result


# ── product detail ────────────────────────────────────────────────────────────


@router.get("/{ean}")
def get_product(
    ean: str = Path(..., pattern=r"^\d{8}$|^\d{13}$"),
    store_id: uuid.UUID | None = Query(None),
    user_lat: float | None = Query(None),
    user_lng: float | None = Query(None),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return get_product_detail(
        db,
        ean=ean,
        user_id=user.id,
        store_id=store_id,
        user_lat=user_lat,
        user_lng=user_lng,
    )
