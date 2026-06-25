"""CRUD operations for optimized_routes."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from ratis_core.database import affected_rows
from ratis_core.models.shopping import OptimizedRoute
from sqlalchemy import select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_route(db: Session, route_id: uuid.UUID) -> OptimizedRoute | None:
    """Get a route by its primary key."""
    return db.get(OptimizedRoute, route_id)


def get_route_for_update(db: Session, route_id: uuid.UUID) -> OptimizedRoute | None:
    """Get a route by primary key with a row-level write lock.

    Used by the synchronous ``move-item`` / ``remove-store`` mutations: both
    rewrite the JSONB ``steps`` column, so two concurrent requests on the same
    route must serialize to avoid a lost update.
    """
    stmt = select(OptimizedRoute).where(OptimizedRoute.id == route_id).with_for_update()
    return db.scalars(stmt).first()


def get_latest_route(db: Session, list_id: uuid.UUID) -> OptimizedRoute | None:
    """Get latest non-expired route for a list."""
    now = datetime.now(UTC)
    stmt = (
        select(OptimizedRoute)
        .where(
            OptimizedRoute.list_id == list_id,
            OptimizedRoute.expires_at > now,
        )
        .order_by(OptimizedRoute.computed_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def get_computing_route(db: Session, list_id: uuid.UUID) -> OptimizedRoute | None:
    """Get a non-expired route still in status='computing' for a list.

    Used as an idempotency guard: a rapid double-tap on optimize must not
    spawn a second 'computing' route + Celery task for the same list.
    """
    now = datetime.now(UTC)
    stmt = (
        select(OptimizedRoute)
        .where(
            OptimizedRoute.list_id == list_id,
            OptimizedRoute.status == "computing",
            OptimizedRoute.expires_at > now,
        )
        .order_by(OptimizedRoute.computed_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def is_expired(route: OptimizedRoute) -> bool:
    """Check whether the route has passed its expiry time."""
    return route.expires_at <= datetime.now(UTC)


def mark_route_failed(db: Session, route_id: uuid.UUID, reason: str | None = None) -> int:
    """Atomically flip a 'computing' route to 'failed'.

    The ``AND status='computing'`` clause is a guard : we MUST NEVER downgrade
    a route that already reached a terminal state (``ready`` / ``failed``).
    Returns the number of rows affected — 0 means the route was already
    terminal, unknown, or somebody else won the race.

    Caller is responsible for ``db.commit()`` (we don't commit here so the
    helper composes cleanly with the surrounding transaction — e.g. the
    endpoint's ghost-reset, where commit is followed by ``create_pending_route``
    in the same logical step).

    The ``reason`` parameter is logged but not persisted — the failure cause
    lives in Sentry breadcrumbs / structured logs, not in the DB column
    (``optimized_routes`` has no ``failure_reason`` column today).
    """
    result = db.execute(
        text("UPDATE optimized_routes SET status = 'failed' WHERE id = :id AND status = 'computing'"),
        {"id": route_id},
    )
    rowcount = affected_rows(result)
    if rowcount:
        logger.info(
            "mark_route_failed: route=%s flipped computing→failed (reason=%s)",
            route_id,
            reason,
        )
    return rowcount
