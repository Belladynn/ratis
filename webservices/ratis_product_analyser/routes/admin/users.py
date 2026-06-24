"""Admin user-scans listing — ARCH_admin_endpoints.md PR6.

One read-only endpoint :

- ``GET /api/v1/admin/users/{user_id}/scans`` — paginated scan list for
  one user. Optional filters (``scan_type``, ``status``, ``since``).
  Joins ``stores.name`` so the operator sees the human label rather
  than the raw ``store_id`` UUID. ``store_name`` is ``None`` when the
  scan has no resolved store (label scans in geo-unknown context).

Auth pattern : ``ADMIN_API_KEY`` only. No TOTP — this is read-only and
never touches financial state. See :func:`ratis_core.deps.verify_admin_key`.

An unknown ``user_id`` returns ``200`` with an empty list (not a 404) —
the endpoint represents a relation, and "no scans for this user" is a
valid resource state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, Query
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_to_start_dt(d: date) -> datetime:
    """``since=YYYY-MM-DD`` → start-of-day UTC for the WHERE clause."""
    return datetime.combine(d, time(0, 0, 0), tzinfo=UTC)


def _serialize_scan(row: Any) -> dict[str, Any]:
    """Per-scan wire shape for the list endpoint.

    Surfaces ``store_name`` from the LEFT JOIN ``stores`` — ``None``
    when the scan has no store_id (label scan in geo-unknown context,
    pending receipt reconciliation).
    """
    return {
        "id": str(row.id),
        "scan_type": row.scan_type,
        "status": row.status,
        "scanned_name": row.scanned_name,
        "product_ean": row.product_ean,
        "store_id": str(row.store_id) if row.store_id else None,
        "store_name": row.store_name,
        "match_method": row.match_method,
        "created_at": row.scanned_at.isoformat() if row.scanned_at else None,
        "image_deleted_at": (row.image_deleted_at.isoformat() if row.image_deleted_at else None),
    }


# ---------------------------------------------------------------------------
# GET /admin/users/{user_id}/scans
# ---------------------------------------------------------------------------


@router.get(
    "/admin/users/{user_id}/scans",
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_user_scans(
    user_id: uuid.UUID,
    scan_type: str | None = Query(default=None, max_length=50),
    status: str | None = Query(default=None, max_length=50),
    since: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a filtered, paginated scan list for one user.

    Filters :
        - ``scan_type`` : ``receipt`` / ``electronic_label`` / ``manual``
          (and any future type — passed through to the SQL ``=``).
        - ``status`` : ``matched`` / ``unresolved`` / ``rejected`` /
          ``pending`` (and legacy v2 values until bloc 8 retirement).
        - ``since`` : ``scans.scanned_at >= start-of-day(date)``.

    Pagination : ``limit`` (1..200, default 50), ``offset`` (>=0).
    Returns ``total`` reflecting the per-user, post-filter count so
    operators can paginate deterministically.

    Errors :
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 422 — invalid query params (Pydantic / FastAPI bounds checks)
    """
    where_parts: list[str] = ["s.user_id = :uid"]
    params: dict[str, Any] = {"uid": str(user_id)}

    if scan_type:
        where_parts.append("s.scan_type = :scan_type")
        params["scan_type"] = scan_type

    if status:
        where_parts.append("s.status = :status")
        params["status"] = status

    if since is not None:
        where_parts.append("s.scanned_at >= :since")
        params["since"] = _date_to_start_dt(since)

    where_clause = " WHERE " + " AND ".join(where_parts)

    # The bandit S608 scanner flags string-built SQL. Every fragment is a
    # constant ; ``where_clause`` is composed only from the closed set of
    # literals above and user input flows exclusively through bound
    # ``:param`` placeholders. Same pattern as routes/admin/stores.py.
    select_cols = (
        "SELECT s.id, s.scan_type, s.status, s.scanned_name, s.product_ean, "
        "       s.store_id, st.name AS store_name, s.match_method, "
        "       s.scanned_at, "
        "       (SELECT image_deleted_at FROM receipts r "
        "        WHERE r.id = s.receipt_id) AS image_deleted_at "
    )
    from_clause = "FROM scans s LEFT JOIN stores st ON st.id = s.store_id"
    count_sql = "SELECT COUNT(*) AS n FROM scans s" + where_clause  # noqa: S608
    list_sql = (
        select_cols + from_clause + where_clause + " ORDER BY s.scanned_at DESC, s.id LIMIT :limit OFFSET :offset"
    )

    total_row = db.execute(text(count_sql), params).first()
    total = int(total_row.n) if total_row is not None else 0

    rows = db.execute(
        text(list_sql),
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    return {
        "scans": [_serialize_scan(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
