"""
Gift card repository — raw SQL queries.

All amounts are INTEGER centimes.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_orders_by_user(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return all gift card orders for a user, newest first."""
    rows = db.execute(
        text(
            "SELECT o.id, o.denomination, o.status, o.source_type, o.source_ref_id, "
            "       o.code, o.issued_at, o.failed_at, o.created_at, "
            "       b.id AS brand_id, b.name AS brand_name, b.logo_url "
            "FROM gift_card_orders o "
            "JOIN gift_card_brands b ON b.id = o.brand_id "
            "WHERE o.user_id = :uid "
            "ORDER BY o.created_at DESC"
        ),
        {"uid": user_id},
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_order(db: Session, order_id: uuid.UUID) -> dict[str, Any] | None:
    """Return a single gift card order by ID, or None."""
    row = db.execute(
        text(
            "SELECT o.id, o.user_id, o.denomination, o.status, o.source_type, o.source_ref_id, "
            "       o.code, o.issued_at, o.failed_at, o.created_at, "
            "       b.id AS brand_id, b.name AS brand_name, b.logo_url "
            "FROM gift_card_orders o "
            "JOIN gift_card_brands b ON b.id = o.brand_id "
            "WHERE o.id = :oid"
        ),
        {"oid": order_id},
    ).first()
    return _row_to_dict(row) if row else None


def insert_gift_card_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    brand_id: uuid.UUID,
    denomination_cents: int,
    source_type: str,
    source_ref_id: str,
) -> uuid.UUID:
    """
    Insert a new gift_card_orders row in 'pending' status.

    Single insert path for every gift-card source (boutique shop_purchase,
    battlepass_milestone, referral_reward, annual_subscription). Idempotent
    via UNIQUE(source_type, source_ref_id) — returns the existing ID on
    conflict. ``denomination_cents`` is INTEGER centimes.
    """
    order_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "    (id, user_id, brand_id, denomination, status, source_type, source_ref_id, created_at) "
            "VALUES (:id, :uid, :bid, :denom, 'pending', :stype, :sref, now()) "
            "ON CONFLICT (source_type, source_ref_id) DO NOTHING"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination_cents,
            "stype": source_type,
            "sref": source_ref_id,
        },
    )
    # Return the actual ID (may differ if conflict)
    row = db.execute(
        text("SELECT id FROM gift_card_orders WHERE source_type = :stype AND source_ref_id = :sref"),
        {"stype": source_type, "sref": source_ref_id},
    ).scalar()
    return row


def sum_redeemed_window(
    db: Session,
    user_id: uuid.UUID,
    *,
    source_type: str,
    window: str,
    exclude_failed: bool = False,
) -> int:
    """Sum gift_card_orders denominations over a Paris-local window.

    Single windowed-SUM path shared by the boutique caps and the cap-usage
    projection. ``window`` ∈ {``"day"``, ``"week"``} — the cap rolls over at
    Paris midnight (``date_trunc(... AT TIME ZONE 'Europe/Paris')``).

    ``exclude_failed`` drops ``status='failed'`` orders : failed orders must
    not consume a cap (a 5xx on the provider should not lock the user out).
    """
    if window not in ("day", "week"):  # defensive — programmer error guard
        raise ValueError(f"Unsupported window: {window!r}")
    # Two fully-literal queries — chosen by ``exclude_failed``. No string
    # concatenation of SQL fragments (S608-clean) ; only the presence of a
    # static WHERE clause varies.
    if exclude_failed:
        sql = (
            "SELECT COALESCE(SUM(denomination), 0) AS total "
            "FROM gift_card_orders "
            "WHERE user_id = :uid "
            "  AND source_type = :stype "
            "  AND status != 'failed' "
            "  AND (created_at AT TIME ZONE 'Europe/Paris') "
            "      >= date_trunc(:win, NOW() AT TIME ZONE 'Europe/Paris')"
        )
    else:
        sql = (
            "SELECT COALESCE(SUM(denomination), 0) AS total "
            "FROM gift_card_orders "
            "WHERE user_id = :uid "
            "  AND source_type = :stype "
            "  AND (created_at AT TIME ZONE 'Europe/Paris') "
            "      >= date_trunc(:win, NOW() AT TIME ZONE 'Europe/Paris')"
        )
    row = db.execute(
        text(sql),
        {"uid": user_id, "stype": source_type, "win": window},
    ).first()
    return int(row.total) if row else 0


def update_order_issued(
    db: Session,
    order_id: uuid.UUID,
    *,
    provider_order_id: str,
    code: str,
) -> bool:
    """Mark a pending order as issued with the provider code.

    The ``status = 'pending'`` guard in the WHERE clause makes this a
    no-op on an order already driven to a terminal state by a concurrent
    issuance — a stale writer can never overwrite an issued/failed order
    (audit RW-money F-1). Returns True if the row was updated.
    """
    result = db.execute(
        text(
            "UPDATE gift_card_orders "
            "SET status = 'issued', provider_order_id = :poid, code = :code, issued_at = now() "
            "WHERE id = :oid AND status = 'pending'"
        ),
        {"poid": provider_order_id, "code": code, "oid": order_id},
    )
    return result.rowcount == 1


def update_order_failed(db: Session, order_id: uuid.UUID) -> bool:
    """Mark a pending order as failed.

    Guarded on ``status = 'pending'`` (see :func:`update_order_issued`) so
    a stale writer cannot flip an already-issued order to failed.
    Returns True if the row was updated.
    """
    result = db.execute(
        text("UPDATE gift_card_orders SET status = 'failed', failed_at = now() WHERE id = :oid AND status = 'pending'"),
        {"oid": order_id},
    )
    return result.rowcount == 1


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": getattr(row, "user_id", None),
        "denomination": row.denomination,
        "status": row.status,
        "source_type": row.source_type,
        "source_ref_id": row.source_ref_id,
        "code": row.code if row.status == "issued" else None,
        "issued_at": row.issued_at,
        "failed_at": row.failed_at,
        "created_at": row.created_at,
        "brand": {
            "id": row.brand_id,
            "name": row.brand_name,
            "logo_url": row.logo_url,
        },
    }
