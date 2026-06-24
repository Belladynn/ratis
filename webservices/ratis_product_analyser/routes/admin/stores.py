"""Admin store-validation endpoints — ARCH_admin_endpoints PR5.

Five endpoints :

- ``GET    /api/v1/admin/stores`` — browse with filters
  (``validation_status`` / ``retailer`` / ``postal_code`` / ``city`` /
  ``search``) + pagination. Read-only ; ``ADMIN_API_KEY`` only.

- ``PATCH  /api/v1/admin/stores/{store_id}/validate`` — force-confirm
  a single store (typically ``user_suggested`` → ``confirmed``).
  ``ADMIN_API_KEY`` + ``X-Admin-Operator``.

- ``POST   /api/v1/admin/stores/validate-bulk`` — atomic bulk
  force-confirm. Returns three buckets : ``validated`` /
  ``skipped_already_confirmed`` / ``not_found``. The whole operation
  runs in one transaction — any INSERT failure rolls everything back.

- ``PATCH  /api/v1/admin/stores/{store_id}/disable`` — soft-delete
  (``is_disabled=true`` + ``disabled_at=now()``). Reason ≥ 3 chars.

- ``PATCH  /api/v1/admin/stores/{store_id}/geocode`` — set ``lat`` /
  ``lng`` manually (typical use : a ``user_suggested`` store arrived
  with ``0/0`` placeholder coords).

Auth pattern (mirrors ``routes.admin.scans``) :

* ``ADMIN_API_KEY`` (Bearer) on every endpoint.
* ``X-Admin-Operator`` header (logged in
  ``store_validation_history.triggered_by``) on the four mutations.
* No 2FA / TOTP — these endpoints touch validation metadata, never
  CAB or cashback. Audit trail is the canonical
  ``store_validation_history`` table.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.exceptions import Conflict, NotFound
from services import store_admin_service
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_operator(x_admin_operator: str | None) -> str:
    """Validate the ``X-Admin-Operator`` header is present + non-empty.

    Returns the operator handle. Raises 400 ``operator_required`` when
    the header is absent / empty (mirrors the rest of the admin router).
    """
    if not x_admin_operator or not x_admin_operator.strip():
        raise HTTPException(status_code=400, detail="operator_required")
    return x_admin_operator.strip()


def _translate_service_exception(exc: Exception) -> HTTPException:
    """Map service-layer exceptions to HTTP statuses.

    Routes own HTTP shape (KP-05) — services raise plain exceptions.
    Centralising the translation here keeps every handler tidy.
    """
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=exc.detail)
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail=exc.detail)
    return HTTPException(status_code=500, detail="internal_server_error")


# ---------------------------------------------------------------------------
# GET /admin/stores
# ---------------------------------------------------------------------------
class StoreListItem(BaseModel):
    """One row in the admin store-browse response.

    Fields mirror the columns most useful for the operator working on
    pending / suspicious / disabled stores. ``lat`` / ``lng`` are
    serialised as floats (Decimal would surface as a string in JSON).
    """

    id: str
    name: str
    retailer: str | None
    address: str | None
    city: str | None
    postal_code: str | None
    lat: float
    lng: float
    is_disabled: bool
    disabled_at: str | None
    validation_status: str
    source: str
    suggested_by_user_id: str | None
    created_at: str
    updated_at: str


class StoreListResponse(BaseModel):
    """Wrapper exposing both the page items and the unfiltered total
    (after applying the same filters except ``limit`` / ``offset``) so
    the operator UI can render pagination controls.
    """

    items: list[StoreListItem]
    total: int
    limit: int
    offset: int


@router.get(
    "/admin/stores",
    dependencies=[Depends(verify_admin_key)],
    response_model=StoreListResponse,
)
def list_stores(
    validation_status: str | None = Query(
        default=None,
        pattern="^(pending|confirmed|suspicious|disabled)$",
    ),
    retailer: str | None = Query(default=None),
    postal_code: str | None = Query(default=None),
    city: str | None = Query(default=None),
    search: str | None = Query(default=None, description="fuzzy text on name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> StoreListResponse:
    """Browse stores filtered by validation status, retailer, location.

    ``validation_status='disabled'`` is a virtual filter (the column is
    ``is_disabled bool``, not part of the lifecycle enum) — it returns
    soft-deleted stores regardless of their underlying validation
    status. The other three values map 1:1 to the ``validation_status``
    column.

    ``search`` does a case-insensitive ``ILIKE`` on the un-accented
    name (uses ``immutable_unaccent`` — already loaded for the v3
    pipeline GENERATED column). ``Marché`` and ``marche`` both match.
    """
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if validation_status == "disabled":
        where.append("is_disabled = true")
    elif validation_status is not None:
        where.append("validation_status = :validation_status")
        where.append("is_disabled = false")
        params["validation_status"] = validation_status
    if retailer is not None:
        where.append("retailer = :retailer")
        params["retailer"] = retailer
    if postal_code is not None:
        where.append("postal_code = :postal_code")
        params["postal_code"] = postal_code
    if city is not None:
        where.append("city = :city")
        params["city"] = city
    if search is not None and search.strip():
        # name_normalized is already UPPER(immutable_unaccent(name)) — see
        # ratis_core/models/store.py. A simple LIKE on it gives accent +
        # case-insensitive matching for free.
        where.append("name_normalized LIKE UPPER(immutable_unaccent(:search_pat))")
        params["search_pat"] = f"%{search.strip()}%"

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    # The bandit S608 scanner flags string-built SQL. Every fragment is a
    # constant ; user input is bound exclusively via ``:param`` placeholders.
    count_sql = "SELECT COUNT(*) AS c FROM stores" + where_clause  # noqa: S608
    total = db.execute(text(count_sql), params).scalar_one()

    list_sql = (
        "SELECT id, name, retailer, address, city, postal_code, "
        "       lat, lng, is_disabled, disabled_at, validation_status, "
        "       source, suggested_by_user_id, created_at, updated_at "
        "FROM stores" + where_clause + " ORDER BY created_at DESC, id "
        "LIMIT :limit OFFSET :offset"
    )
    rows = db.execute(text(list_sql), params).fetchall()

    items = [
        StoreListItem(
            id=str(r.id),
            name=r.name,
            retailer=r.retailer,
            address=r.address,
            city=r.city,
            postal_code=r.postal_code,
            lat=float(r.lat),
            lng=float(r.lng),
            is_disabled=bool(r.is_disabled),
            disabled_at=r.disabled_at.isoformat() if r.disabled_at else None,
            validation_status=r.validation_status,
            source=r.source,
            suggested_by_user_id=(str(r.suggested_by_user_id) if r.suggested_by_user_id else None),
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]
    return StoreListResponse(
        items=items,
        total=int(total),
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# PATCH /admin/stores/{store_id}/validate
# ---------------------------------------------------------------------------
@router.patch(
    "/admin/stores/{store_id}/validate",
    dependencies=[Depends(verify_admin_key)],
)
def validate_store(
    store_id: uuid.UUID,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Force-confirm one store ; logs an ``admin_validate`` history row.

    Errors :
        - 400 ``operator_required``
        - 403 ``forbidden`` (wrong / missing ADMIN_API_KEY)
        - 404 ``store_not_found``
        - 409 ``store_already_confirmed``
    """
    operator = _require_operator(x_admin_operator)
    try:
        result = store_admin_service.validate_store(db, store_id, operator)
    except (NotFound, Conflict) as exc:
        raise _translate_service_exception(exc) from exc
    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /admin/stores/validate-bulk
# ---------------------------------------------------------------------------
class BulkValidateRequest(BaseModel):
    """Payload for ``POST /admin/stores/validate-bulk``.

    ``min_length=1`` enforces a non-empty list at validation time —
    an empty bulk is a usage error (the operator forgot to select rows).
    """

    ids: list[uuid.UUID] = Field(min_length=1)

    model_config = {"extra": "forbid"}


class BulkValidateResponse(BaseModel):
    validated: list[str]
    skipped_already_confirmed: list[str]
    not_found: list[str]


@router.post(
    "/admin/stores/validate-bulk",
    dependencies=[Depends(verify_admin_key)],
    response_model=BulkValidateResponse,
)
def validate_stores_bulk(
    body: BulkValidateRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> BulkValidateResponse:
    """Atomically validate a list of stores.

    The whole bucketing + UPDATEs + history INSERTs runs in the
    caller's transaction. If any INSERT raises, FastAPI's exception
    handler propagates → SQLAlchemy rolls back → no partial state.
    Idempotent on already-confirmed ids (they land in
    ``skipped_already_confirmed`` ; no history written).

    Errors :
        - 400 ``operator_required``
        - 403 ``forbidden``
        - 422 — empty ``ids``
    """
    operator = _require_operator(x_admin_operator)
    result = store_admin_service.validate_stores_bulk(db, body.ids, operator)
    db.commit()
    return BulkValidateResponse(**result)


# ---------------------------------------------------------------------------
# PATCH /admin/stores/{store_id}/disable
# ---------------------------------------------------------------------------
class DisableStoreRequest(BaseModel):
    """Payload for the disable endpoint.

    A reason of at least 3 chars is required so the audit trail always
    captures *why* the store was disabled (closure / abuse / duplicate).
    """

    reason: str = Field(min_length=3, max_length=500)

    model_config = {"extra": "forbid"}


@router.patch(
    "/admin/stores/{store_id}/disable",
    dependencies=[Depends(verify_admin_key)],
)
def disable_store(
    store_id: uuid.UUID,
    body: DisableStoreRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Soft-delete a store. ``validation_status`` is left untouched.

    Errors :
        - 400 ``operator_required``
        - 403 ``forbidden``
        - 404 ``store_not_found``
        - 409 ``store_already_disabled``
        - 422 — reason < 3 chars
    """
    operator = _require_operator(x_admin_operator)
    try:
        result = store_admin_service.disable_store(db, store_id, body.reason, operator)
    except (NotFound, Conflict) as exc:
        raise _translate_service_exception(exc) from exc
    db.commit()
    return result


# ---------------------------------------------------------------------------
# PATCH /admin/stores/{store_id}/geocode
# ---------------------------------------------------------------------------
class GeocodeStoreRequest(BaseModel):
    """Payload for the geocode endpoint.

    Pydantic enforces the WGS84 coordinate ranges before the service
    layer is reached. The endpoint is most useful on ``user_suggested``
    stores that arrived with ``lat=0 lng=0`` placeholders.
    """

    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)

    model_config = {"extra": "forbid"}


@router.patch(
    "/admin/stores/{store_id}/geocode",
    dependencies=[Depends(verify_admin_key)],
)
def geocode_store(
    store_id: uuid.UUID,
    body: GeocodeStoreRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Set ``lat`` / ``lng`` on a store ; logs an ``admin_geocode`` row.

    Errors :
        - 400 ``operator_required``
        - 403 ``forbidden``
        - 404 ``store_not_found``
        - 422 — lat / lng out of WGS84 range
    """
    operator = _require_operator(x_admin_operator)
    try:
        result = store_admin_service.geocode_store(db, store_id, body.lat, body.lng, operator)
    except NotFound as exc:
        raise _translate_service_exception(exc) from exc
    db.commit()
    return result
