"""Admin user lookup endpoints — ARCH_admin_endpoints.md PR6.

Two read-only endpoints for support / investigation :

- ``GET /api/v1/admin/users``
    Paginated list with optional filters (``email_contains``,
    ``created_since``, ``is_deleted``). Excludes soft-deleted users by
    default. Returns minimal summary fields per user — never any secret.

- ``GET /api/v1/admin/users/{user_id}``
    Full profile for one user (always returned even when soft-deleted —
    the support escape hatch for post-anonymize investigation). Surfaces
    aggregates pulled from the shared PG (refresh tokens active count,
    most-recent subscription status, cashback withdrawal count).

Auth pattern : ``ADMIN_API_KEY`` only. No TOTP — these endpoints are
read-only and do not touch financial state. See
:func:`ratis_core.deps.verify_admin_key`.

Security posture :

- ``password_hash`` is **never** included in any payload.
- Refresh-token / OAuth raw tokens are never exposed — we expose only
  the count of currently-active refresh tokens.
- Tests assert both the structured shape (no forbidden keys) AND the
  raw response text (defense in depth).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


# ``support_id`` lookup uses an exact-match equality. We pre-validate the
# format at the route layer so a malformed query short-circuits with 422
# instead of running an empty-result SELECT against PG.
_SUPPORT_ID_RE = re.compile(r"^RTS-[A-HJ-NP-Z2-9]{6}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_to_start_dt(d: date) -> datetime:
    """``created_since=YYYY-MM-DD`` → start-of-day UTC for the WHERE clause."""
    return datetime.combine(d, time(0, 0, 0), tzinfo=UTC)


def _serialize_summary(row: Any) -> dict[str, Any]:
    """Stable summary shape for the list endpoint.

    Only fields safe for an operator listing : id, email, created_at,
    is_deleted, account_type. ``password_hash`` and any token-shaped data
    are intentionally NOT selected by the SQL — defense in depth on top
    of the schema-level filtering.
    """
    return {
        "id": str(row.id),
        "email": row.email,
        "support_id": row.support_id,
        "account_type": row.account_type,
        "is_deleted": bool(row.is_deleted),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /admin/users — paginated list
# ---------------------------------------------------------------------------


@router.get(
    "/admin/users",
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_users(
    email_contains: str | None = Query(default=None, max_length=200),
    support_id: str | None = Query(default=None, max_length=10),
    created_since: date | None = Query(default=None),
    is_deleted: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a filtered, paginated user list (summary fields only).

    Filters :
        - ``email_contains`` : case-insensitive ``ILIKE %s%`` on email.
        - ``support_id`` : exact match on the public ``RTS-XXXXXX``
          identifier — direct lookup for support workflows. Mutually
          exclusive with ``email_contains`` (operator picks one lookup
          shape) — passing both yields 422.
        - ``created_since`` : ``users.created_at >= start-of-day(date)``.
        - ``is_deleted`` : when ``False`` (default) excludes soft-deleted
          users ; when ``True`` returns ONLY soft-deleted users so an
          operator can audit anonymized accounts in isolation.

    Pagination : ``limit`` (1..200, default 50), ``offset`` (>=0). The
    ``total`` field reflects the count under the same filters (not the
    overall users table size) so operators can paginate deterministically.

    Errors :
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 422 — invalid query params (Pydantic / FastAPI bounds checks),
          or both ``email_contains`` and ``support_id`` provided.
    """
    # Mutual exclusion : an operator looks up by ONE handle at a time.
    # Surfacing this at the route layer (rather than implicitly ANDing
    # the two filters in SQL) makes the contract explicit for the admin
    # UI and catches operator typos early.
    if email_contains is not None and support_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="email_contains_and_support_id_mutually_exclusive",
        )

    # Pre-validate the support_id format — the alphabet/length is fixed
    # so a malformed value is always a client error, not "no row found".
    if support_id is not None and not _SUPPORT_ID_RE.match(support_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid_support_id_format",
        )

    # Build the parameterised WHERE clause once and reuse for SELECT + COUNT.
    where_parts: list[str] = []
    params: dict[str, Any] = {}

    if is_deleted:
        where_parts.append("is_deleted = TRUE")
    else:
        where_parts.append("is_deleted = FALSE")

    if email_contains:
        where_parts.append("email ILIKE :email_pattern")
        params["email_pattern"] = f"%{email_contains}%"

    if support_id:
        where_parts.append("support_id = :support_id")
        params["support_id"] = support_id

    if created_since is not None:
        where_parts.append("created_at >= :created_since")
        params["created_since"] = _date_to_start_dt(created_since)

    where_clause = " WHERE " + (" AND ".join(where_parts) if where_parts else "TRUE")

    # The bandit S608 scanner flags string-built SQL. Every fragment is a
    # constant ; ``where_clause`` is composed only from the closed set of
    # literals above and user input flows exclusively through bound
    # ``:param`` placeholders. Same pattern as routes/admin/stores.py.
    select_cols = "SELECT id, email, support_id, account_type, is_deleted, created_at "
    count_sql = "SELECT COUNT(*) AS n FROM users" + where_clause  # noqa: S608
    list_sql = select_cols + "FROM users" + where_clause + " ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"

    total_row = db.execute(text(count_sql), params).first()
    total = int(total_row.n) if total_row is not None else 0

    rows = db.execute(
        text(list_sql),
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    return {
        "users": [_serialize_summary(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# GET /admin/users/{user_id} — full profile
# ---------------------------------------------------------------------------


@router.get(
    "/admin/users/{user_id}",
    dependencies=[Depends(verify_admin_key)],
)
def admin_get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a full profile for one user, plus useful aggregates.

    Aggregates are pulled directly from the shared PG cluster :
    - ``refresh_tokens_active`` : refresh_tokens not revoked AND not expired.
    - ``subscription_status`` : status of the most-recently-started
      subscription row (``ORDER BY started_at DESC LIMIT 1``). ``None`` if
      no row exists. Subscriptions are ``NEVER PURGE`` (legal) so this is
      always authoritative.
    - ``cashback_withdrawal_count`` : total rows in cashback_withdrawals.

    Soft-deleted users are returned by this endpoint — it is the support
    escape hatch for post-anonymize investigation.

    Errors :
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``user_not_found`` — no row for ``user_id``
    """
    user_row = db.execute(
        text(
            "SELECT id, email, support_id, account_type, display_name, "
            "       avatar_url, is_deleted, timezone, password_changed_at, "
            "       created_at, updated_at "
            "FROM users WHERE id = :uid"
        ),
        {"uid": str(user_id)},
    ).first()

    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )

    # Active refresh tokens — not revoked AND not yet expired.
    rt_row = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM refresh_tokens "
            "WHERE user_id = :uid AND revoked_at IS NULL AND expires_at > now()"
        ),
        {"uid": str(user_id)},
    ).first()
    refresh_tokens_active = int(rt_row.n) if rt_row is not None else 0

    # Most-recent subscription — single row, NULL if user never subscribed.
    sub_row = db.execute(
        text("SELECT status FROM subscriptions WHERE user_id = :uid ORDER BY started_at DESC LIMIT 1"),
        {"uid": str(user_id)},
    ).first()
    subscription_status = sub_row.status if sub_row is not None else None

    # Cashback withdrawal count — NEVER PURGE table, count is stable.
    cw_row = db.execute(
        text("SELECT COUNT(*) AS n FROM cashback_withdrawals WHERE user_id = :uid"),
        {"uid": str(user_id)},
    ).first()
    cashback_withdrawal_count = int(cw_row.n) if cw_row is not None else 0

    return {
        "id": str(user_row.id),
        "email": user_row.email,
        "support_id": user_row.support_id,
        "account_type": user_row.account_type,
        "display_name": user_row.display_name,
        "avatar_url": user_row.avatar_url,
        "is_deleted": bool(user_row.is_deleted),
        "timezone": user_row.timezone,
        "password_changed_at": (user_row.password_changed_at.isoformat() if user_row.password_changed_at else None),
        "created_at": user_row.created_at.isoformat() if user_row.created_at else None,
        "updated_at": user_row.updated_at.isoformat() if user_row.updated_at else None,
        "refresh_tokens_active": refresh_tokens_active,
        "subscription_status": subscription_status,
        "cashback_withdrawal_count": cashback_withdrawal_count,
    }
