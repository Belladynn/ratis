"""Celery tasks for ratis_list_optimiser.

Each task creates its own DB session and delegates to the service layer.
Tests call run_optimize_route directly to avoid needing a running Celery broker.
"""

from __future__ import annotations

import logging
import os
import uuid

import sentry_sdk
from celery.exceptions import Retry
from ratis_core.database import make_engine
from sqlalchemy.orm import Session

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_S = 30


def _make_session() -> tuple[object, Session]:
    """Create a disposable engine + session for a worker task."""
    engine = make_engine(os.environ["DATABASE_URL"])
    return engine, Session(engine)


def _safe_mark_failed(db: Session, route_id: uuid.UUID, reason: str) -> None:
    """Best-effort 'computing → failed' flip from the worker's finally path.

    Wraps :func:`repositories.route_repository.mark_route_failed` so that any
    secondary failure (e.g. DB already in error state) does NOT mask the
    original exception. We log it and move on — the route is at worst stuck
    in 'computing' (the very state we were trying to avoid), and we've now
    sent an extra Sentry signal that the rescue itself failed, which is
    actionable.

    Per KP-20, we rollback FIRST in case the session is in
    ``PendingRollbackError`` state from the original crash.
    """
    from repositories import route_repository as route_repo

    try:
        try:
            db.rollback()  # KP-20 — clear any pending error state first
        except Exception:
            logger.exception("rollback before mark_failed itself failed (route=%s)", route_id)
        n = route_repo.mark_route_failed(db, route_id, reason=reason)
        db.commit()
        if n == 0:
            # Already terminal (or unknown id) — no harm done, just note it.
            logger.info(
                "task_optimize_route: route %s was already terminal at outer-guard time (reason=%s)",
                route_id,
                reason,
            )
    except Exception:
        logger.exception(
            "task_optimize_route: outer-guard mark_failed itself crashed for route %s",
            route_id,
        )
        sentry_sdk.capture_exception()


@celery_app.task(
    name="ratis_list_optimiser.optimize_route",
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_DELAY_S,
)
def task_optimize_route(self, route_id: str, lat: float, lng: float) -> None:
    """Compute the optimized route and finalize it.

    Retries up to 3× (30 s apart) on transient failures before giving up.
    Permanent failures (NoStoresNearby, …) are handled inside run_optimize_route
    and do not trigger a retry.

    The OUTER try/except/finally is the last line of defense against the
    ``status='computing'`` stuck-route class of bugs (Sentry
    RATIS-WEBSERVICES-18). If anything between the Celery dispatch and a
    terminal status leak — an import error, a crash in ``_make_session``, a
    crash in the very first line of ``run_optimize_route``, a crash inside
    ``self.retry()`` itself — the outer ``finally`` always runs and ensures
    the route is at least marked ``failed``. Without this, the user is
    stuck behind the partial unique index forever.

    Celery's ``Retry`` exception is a control-flow signal, NOT a failure —
    we re-raise it so Celery schedules the next attempt; the route stays
    'computing' (which is correct: a retry will follow).
    """
    from services.optimization_service import run_optimize_route

    route_uuid = uuid.UUID(route_id)
    engine, db = _make_session()
    needs_outer_mark_failed = True
    try:
        try:
            run_optimize_route(db, route_uuid, lat=lat, lng=lng)
            # Success path : run_optimize_route already flipped the route to
            # 'ready' (or 'failed' on a permanent OptimizationError) and
            # committed. The outer guard has nothing to do.
            needs_outer_mark_failed = False
        except Retry:
            # Celery control-flow — let it bubble, the worker will reschedule.
            # The route legitimately stays in 'computing' until the retry runs.
            needs_outer_mark_failed = False
            raise
        except Exception as exc:
            logger.warning(
                "task_optimize_route: attempt %d/%d failed for route %s: %s",
                self.request.retries + 1,
                self.max_retries + 1,
                route_id,
                exc,
            )
            if self.request.retries < self.max_retries:
                try:
                    # ``self.retry`` raises ``Retry`` on success — caught above.
                    raise self.retry(exc=exc)
                except Retry:
                    needs_outer_mark_failed = False
                    raise
                # If self.retry() itself raised something else (Celery internal
                # error, broker down…) we fall through to the outer finally
                # and mark failed there.
            # All retries exhausted — mark failed via the repo helper so the
            # transition goes through the single guarded UPDATE path.
            _safe_mark_failed(db, route_uuid, reason=f"retries_exhausted:{type(exc).__name__}")
            sentry_sdk.capture_exception(exc)
            needs_outer_mark_failed = False
    except Retry:
        raise
    except Exception as outer_exc:
        # Truly unexpected — something exploded outside the inner try. The
        # outer finally below will fire too; we just log here for visibility.
        logger.exception("task_optimize_route: outer-level exception for route %s", route_id)
        sentry_sdk.capture_exception(outer_exc)
        # Don't re-raise — the route is what matters; Celery seeing an
        # exception here would trigger an undesired implicit retry storm.
    finally:
        if needs_outer_mark_failed:
            # Last-chance safety net : if we never reached a terminal status
            # via the normal paths, force the route out of 'computing'.
            _safe_mark_failed(db, route_uuid, reason="outer_guard_unhandled")
        try:
            db.close()
        except Exception:
            logger.exception("db.close() failed for route %s", route_id)
        try:
            engine.dispose()
        except Exception:
            logger.exception("engine.dispose() failed for route %s", route_id)
