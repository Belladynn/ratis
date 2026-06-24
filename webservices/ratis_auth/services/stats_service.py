"""Aggregate user statistics for the Profil screen + dashboard ROI rings.

Cross-service read: the account service reads `scans` + `price_consensus` +
`stores` (owned by other services) because all services share one DB. No writes
other than the idempotent UPSERT that materialises a ``user_savings_snapshot``
row on first access.

Hybrid snapshot :
- The nightly ratis_batch_savings computes ``lifetime_savings_cents`` into
  ``user_savings_snapshot`` and stamps ``last_computed_at``.
- Each /account/stats call returns ``snapshot.lifetime + live_delta`` where
  ``live_delta`` is recomputed from scans since ``last_computed_at``.
- ``today_savings_cents`` is always a live recompute for scans since
  ``CURRENT_DATE`` (user-local TZ is ignored in V1 — UTC midnight).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ratis_core.models.user import User
from ratis_core.savings import compute_savings_for_user
from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session


def _read_subscription_price_cents() -> int:
    """Load the subscription price (cents) from ratis_settings. Fail-fast if missing."""
    settings = load_settings()
    savings_cfg = settings.get("savings")
    if not savings_cfg or "subscription_price_cents" not in savings_cfg:
        raise RuntimeError(
            "ratis_settings.savings.subscription_price_cents is missing — "
            "seed app_settings or update ratis_settings.json"
        )
    price = int(savings_cfg["subscription_price_cents"])
    if price <= 0:
        raise RuntimeError(f"ratis_settings.savings.subscription_price_cents must be > 0, got {price}")
    return price


def _ensure_snapshot(db: Session, user_id) -> dict:
    """
    Return the snapshot row for user_id. If missing, initialize it with the
    current lifetime value (live compute) and insert the row. Always commits
    the insert so the snapshot survives the request even if later code rolls
    back — the snapshot is an idempotent materialization, never a state change.
    """
    row = (
        db.execute(
            text(
                "SELECT lifetime_savings_cents, rings_consumed, last_computed_at "
                "FROM user_savings_snapshot WHERE user_id = :uid"
            ),
            {"uid": str(user_id)},
        )
        .mappings()
        .one_or_none()
    )

    if row is not None:
        return {
            "lifetime_savings_cents": int(row["lifetime_savings_cents"]),
            "rings_consumed": int(row["rings_consumed"]),
            "last_computed_at": row["last_computed_at"],
        }

    # First access — live-compute and materialise.
    lifetime = compute_savings_for_user(db, user_id, since=None)
    now = datetime.now(UTC)
    db.execute(
        text(
            "INSERT INTO user_savings_snapshot "
            "(user_id, lifetime_savings_cents, rings_consumed, last_computed_at, updated_at) "
            "VALUES (:uid, :v, 0, :now, :now) "
            "ON CONFLICT (user_id) DO NOTHING"
        ),
        {"uid": str(user_id), "v": lifetime, "now": now},
    )
    db.commit()
    return {
        "lifetime_savings_cents": lifetime,
        "rings_consumed": 0,
        "last_computed_at": now,
    }


def compute_account_stats(db: Session, user: User) -> dict:
    """Return aggregated stats for the given user.

    Returns
    -------
    dict
        ``total_scans`` / ``unique_products`` — lifetime counts.
        ``total_savings_cents`` — snapshot + live delta (cents).
        ``today_savings_cents`` — live compute, scans since UTC midnight.
        ``location_missing`` — True if ``users.ref_lat`` IS NULL.
        ``member_since`` — ISO timestamp of ``users.created_at``.
        ``rings`` — ``{rings_consumed, pending_rings, subscription_price_cents}``.
    """
    row = (
        db.execute(
            text(
                "SELECT "
                "  COUNT(*) AS total_scans, "
                "  COUNT(DISTINCT product_ean) FILTER ("
                "    WHERE status = 'accepted' AND product_ean IS NOT NULL"
                "  ) AS unique_products "
                "FROM scans WHERE user_id = :uid"
            ),
            {"uid": str(user.id)},
        )
        .mappings()
        .one()
    )

    location_missing = user.ref_lat is None

    snapshot = _ensure_snapshot(db, user.id)
    fresh_delta = compute_savings_for_user(db, user.id, since=snapshot["last_computed_at"])
    total_savings_cents = snapshot["lifetime_savings_cents"] + fresh_delta

    today_midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_savings_cents = compute_savings_for_user(db, user.id, since=today_midnight)

    subscription_price_cents = _read_subscription_price_cents()
    eligible = total_savings_cents // subscription_price_cents
    pending_rings = max(0, eligible - snapshot["rings_consumed"])

    return {
        "total_scans": int(row["total_scans"] or 0),
        "unique_products": int(row["unique_products"] or 0),
        "total_savings_cents": int(total_savings_cents),
        "today_savings_cents": int(today_savings_cents),
        "location_missing": location_missing,
        "member_since": user.created_at.isoformat() if user.created_at else None,
        "rings": {
            "rings_consumed": int(snapshot["rings_consumed"]),
            "pending_rings": int(pending_rings),
            "subscription_price_cents": subscription_price_cents,
        },
    }
