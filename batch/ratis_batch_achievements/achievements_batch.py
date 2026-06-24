"""Achievements V1 batch — nightly catalog × users sweep.

Runs through:

  1. All active achievements (within their ``available_from``/``available_until``
     window — NULL bounds mean "open").
  2. All non-banned, non-deleted users.
  3. For each (user, achievement) pair, dispatch to the matching handler in
     ``TRIGGER_HANDLERS`` (event-path) ∪ ``_BATCH_ONLY_HANDLERS`` (batch-only,
     currently ``savings_eur_in_window`` which the event dispatcher excludes
     via ``WINDOWED_TRIGGER_TYPES``).
  4. On True, attempt to unlock via ``_unlock`` (idempotent — UNIQUE
     constraint + ``ON CONFLICT DO NOTHING``). Returns ``True`` only when a
     fresh row was inserted, so re-running the batch over already-unlocked
     achievements is a no-op.

Defence-in-depth :

* The ``shadow_banned`` / ``deleted`` filter is applied at the SELECT layer
  here, not just relied upon via the event-driven dispatcher. This costs one
  extra WHERE clause and immunises the batch against any future schema where
  ``check_achievements`` no longer fronts every unlock.
* Per-handler exceptions are isolated — one buggy handler does not poison
  the rest of the run. They count toward ``BatchResult.errors`` and surface
  via Sentry / structured logs.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Batch nightly.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ratis_core.models.achievement import Achievement, UserAchievement
from ratis_core.models.user import User
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

# achievement_service uses flat-layout imports (``from repositories.cab_repository
# import award_cab``) that resolve via webservices/ratis_rewards/ on sys.path.
# The rewards service container ships with this layout ; here in the batch we
# add the directory defensively so ``from webservices.ratis_rewards.services...``
# transitively resolves its ``from repositories...`` imports.
_REWARDS_DIR = Path(__file__).resolve().parents[2] / "webservices" / "ratis_rewards"
if _REWARDS_DIR.is_dir() and str(_REWARDS_DIR) not in sys.path:
    sys.path.insert(0, str(_REWARDS_DIR))

from webservices.ratis_rewards.services.achievement_service import (
    _BATCH_ONLY_HANDLERS,
    TRIGGER_HANDLERS,
    _unlock,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    success: bool
    rows_affected: int
    errors: int = 0


def run_batch(db: Session) -> BatchResult:
    """Iterate (eligible_users × eligible_achievements) and unlock matches.

    Returns a ``BatchResult`` summarising the run :

    * ``success`` — ``True`` when no handler raised an exception. Per-handler
      crashes are logged + counted but do NOT abort the run.
    * ``rows_affected`` — number of NEW ``user_achievements`` rows inserted.
      Already-unlocked rows are silent no-ops (idempotent ``_unlock``).
    * ``errors`` — number of handler exceptions caught.
    """
    now = datetime.now(UTC)

    achievements: list[Achievement] = list(
        db.scalars(
            select(Achievement).where(
                or_(
                    Achievement.available_from.is_(None),
                    Achievement.available_from <= now,
                ),
                or_(
                    Achievement.available_until.is_(None),
                    Achievement.available_until > now,
                ),
            )
        ).all()
    )

    user_ids: list[UUID] = list(
        db.scalars(
            select(User.id).where(
                User.is_shadow_banned.is_(False),
                User.is_deleted.is_(False),
            )
        ).all()
    )

    rows_unlocked = 0
    errors = 0

    for user_id in user_ids:
        # One round-trip per user to learn what's already unlocked — avoids
        # redundant handler evaluation on the hot path.
        already: set[UUID] = set(
            db.scalars(select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id)).all()
        )

        for ach in achievements:
            if ach.id in already:
                continue

            handler = TRIGGER_HANDLERS.get(ach.trigger_type) or _BATCH_ONLY_HANDLERS.get(ach.trigger_type)
            if handler is None:
                # No registered handler for this trigger — skip silently.
                # ``_eval_unique_products_discovered_count`` is intentionally
                # registered but always returns False ; that's a registered
                # handler, not a missing one.
                continue

            try:
                threshold_met = handler(
                    db,
                    user_id,
                    float(ach.target_value),
                    ach.window_days,
                    ach.extra_params or {},
                )
                if threshold_met and _unlock(db, user_id, ach, trigger_event={"source": "batch_nightly"}):
                    rows_unlocked += 1
            except Exception:
                logger.exception(
                    "achievement_batch_handler_failed",
                    extra={
                        "achievement_code": ach.code,
                        "trigger_type": ach.trigger_type,
                        "user_id": str(user_id),
                    },
                )
                # Roll back any partial state from the failing handler so the
                # next iteration starts from a clean session — mirrors the
                # safety net in ``check_achievements``.
                db.rollback()
                errors += 1

    return BatchResult(
        success=errors == 0,
        rows_affected=rows_unlocked,
        errors=errors,
    )
