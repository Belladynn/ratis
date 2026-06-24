"""Achievement service — dispatcher + handlers + unlock.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Service & dispatcher.

Public surface :

* ``EVENT_TYPE_TO_TRIGGERS`` : event_type → set of trigger_type evaluated on this event.
* ``WINDOWED_TRIGGER_TYPES`` : triggers reserved for the nightly batch (skipped event-path).
* ``TRIGGER_HANDLERS`` : trigger_type → handler callable (event-path).
* ``_BATCH_ONLY_HANDLERS`` : trigger_type → handler callable (batch-path only).
* ``check_achievements(db, user_id, event_type, payload)`` : event-driven dispatcher.
* ``_unlock(db, user_id, ach, trigger_event)`` : transactional unlock + CAB grant.

The dispatcher is fire-and-forget — exceptions are caught + reported to Sentry but
NEVER propagated to the caller. The caller (cab_service / cashback_service / etc.)
must keep working even if achievement_service has a bug.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sentry_sdk
from ratis_core.models.achievement import Achievement, UserAchievement
from ratis_core.models.user import User
from sqlalchemy import and_, distinct, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Scans considered "successful" for achievement counting. ``matched`` is the
# pipeline status ; ``accepted`` is the legacy v2 status — both still appear
# in production data. Keep both literal strings here (single source of truth)
# rather than scattering them across handlers.
_ACCEPTED_SCAN_STATUSES: tuple[str, ...] = ("matched", "accepted")


# Event-driven dispatch map. Each new event_type adds an entry here.
EVENT_TYPE_TO_TRIGGERS: dict[str, set[str]] = {
    "scan_accepted": {
        "scan_count",
        "unique_brands_count",
        "unique_categories_count",
        "unique_products_discovered_count",
    },
    "cashback_credited": {"savings_eur_total"},
    "streak_extended": {"streak_days"},
    "referral_paid": {"referral_count"},
    "battlepass_season_participated": {"first_event"},
    "konami_code_entered": {"first_event"},
    "app_opened_at_3am": {"first_event"},
}

# Triggers réservés au batch nightly (jamais event-driven).
WINDOWED_TRIGGER_TYPES: set[str] = {"savings_eur_in_window"}


# Handler signature : (db, user_id, target, window_days, extra) -> bool
HandlerFn = Callable[[Session, UUID, float, int | None, dict], bool]

# Computer signature : (db, user_id, window_days, extra) -> int | float
# ``compute`` returns the *current scalar value* for a user/trigger pair.
# Threshold comparison happens upstream in the matching ``_eval_*`` handler.
ComputerFn = Callable[[Session, UUID, int | None, dict], int | float]


# ---------------------------------------------------------------------------
# Compute primitives (V1.1) — generic SQL building blocks shared by the
# handlers (``_eval_*``) and the progress computers (``_compute_*``).
#
# Returning a *scalar value* (not a bool) is the whole point — the same
# primitive powers (a) the bool threshold comparison the dispatcher needs
# to decide whether to unlock and (b) the live progress value the
# serializer surfaces to the FE for the X/Y bar (KP-76).
#
# The primitives accept arbitrary SQLAlchemy ``where()`` clauses via
# ``*extra_where`` — that keeps them strongly-typed (caller passes
# ``Scan.status.in_(...)`` not a stringly-typed dict) while still being
# perfectly generic. ``_count_distinct_for_user`` takes a pre-built
# SELECT chain because the JOIN cases (categories) cannot be expressed
# as a bare ``model + where`` pair.
# ---------------------------------------------------------------------------


def _count_for_user(
    db: Session,
    user_id: UUID,
    model: Any,
    *extra_where: Any,
) -> int:
    """Generic ``COUNT(*) FROM <model> WHERE user_id = :uid AND <extra_where>``.

    All callers expect the model to expose a ``user_id`` column — which all
    achievement-relevant tables do (scans, gift_card_orders, products via
    ``first_discovered_by_user_id``, ...). The ``user_id`` filter is added
    here so callers cannot accidentally forget it.
    """
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id, *extra_where)) or 0


def _sum_for_user(
    db: Session,
    user_id: UUID,
    model: Any,
    column: Any,
    *extra_where: Any,
) -> int:
    """Generic ``SUM(<column>) FROM <model> WHERE user_id = :uid AND <extra_where>``.

    ``coalesce(..., 0)`` ensures we always return an int (not None) — the
    threshold comparison in the eval handler relies on this.
    """
    return db.scalar(select(func.coalesce(func.sum(column), 0)).where(model.user_id == user_id, *extra_where)) or 0


def _count_distinct_for_user(
    db: Session,
    user_id: UUID,
    distinct_column: Any,
    base_select: Any,
) -> int:
    """Generic ``COUNT(DISTINCT <distinct_column>)`` over a caller-built SELECT.

    Unlike the simpler ``_count_for_user`` / ``_sum_for_user``, this primitive
    cannot infer the ``WHERE user_id = :uid`` clause itself : the JOIN
    expressions (e.g. ``Scan JOIN Product``) require the caller to specify
    which side carries ``user_id``. So the caller passes a fully-built
    ``select(...).select_from(...).where(...)`` chain ; this helper merely
    wraps the SELECT-list with ``COUNT(DISTINCT ...)``.

    The signature still includes ``user_id`` for symmetry and future-proofing
    (so all 5 primitives have a uniform shape and a future linter could
    enforce its presence in callers). It is intentionally unused in the body.
    """
    del user_id  # see docstring — caller embeds the filter in base_select.
    return db.scalar(base_select.with_only_columns(func.count(distinct(distinct_column)))) or 0


def _max_streak_for_user(db: Session, user_id: UUID) -> int:
    """Read ``user_streaks.current_streak_days`` denorm — Feed Jack.

    Returns 0 when the user has no row yet (lazy materialisation on first
    feed event). Mirrors ``_eval_streak_days``'s "no row → 0" semantics.
    """
    from ratis_core.models.gamification import UserStreak

    streak = db.get(UserStreak, user_id)
    if streak is None:
        return 0
    return int(streak.current_streak_days or 0)


def _first_event_seen(db: Session, user_id: UUID, event_type: str) -> bool:
    """Placeholder for the ``first_event`` trigger family.

    Always returns True. The "has the user already triggered this event"
    discrimination is enforced upstream :

    * ``check_achievements`` filters candidates by the SQL clause
      ``Achievement.extra_params['event'].astext == event_type`` so only
      one ``first_event`` row is even considered.
    * The ``NOT EXISTS user_achievements`` clause in the same SELECT
      proves the user has not already unlocked it.

    By the time we are called, the unlock is justified — return True
    unconditionally. Kept as a primitive (rather than inlining ``return
    True``) to make the handler ↔ primitive mapping uniform : every eval
    handler delegates its logic to a primitive.
    """
    del db, user_id, event_type  # see docstring — discrimination is upstream.
    return True


# ---------------------------------------------------------------------------
# Handlers (uniform signature : db, user_id, target, window_days, extra → bool)
# ---------------------------------------------------------------------------


def _compute_scan_count(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Current scan_count value for the user.

    Both legacy ``accepted`` and v3 ``matched`` statuses count — see
    ``_ACCEPTED_SCAN_STATUSES`` rationale at the top of this module.

    ``extra.window_scans_only`` (+ ``extra.window_since_iso``) optionally
    restricts the count to scans created since a given timestamp — useful
    for seasonal "5 scans during Halloween 2026" achievements.

    ``window_days`` is unused here (event-driven trigger ; the windowed
    variant lives in ``_eval_savings_eur_in_window``). Kept in the signature
    so the dispatcher and progress computer can call uniformly.
    """
    del window_days  # see docstring — windowing is via extra.window_*.
    from ratis_core.models.scan import Scan

    where_clauses = [Scan.status.in_(_ACCEPTED_SCAN_STATUSES)]
    if extra.get("window_scans_only") and (since := extra.get("window_since_iso")):
        where_clauses.append(Scan.scanned_at >= since)
    return _count_for_user(db, user_id, Scan, *where_clauses)


def _eval_scan_count(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_scan_count``."""
    return _compute_scan_count(db, user_id, window_days, extra) >= int(target)


def _compute_savings_eur_total(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Sum of CREDIT cashback (cents) for the user across all time.

    Counts ``pending`` + ``confirmed`` rows ; ``refused`` rows are excluded
    explicitly (the data model updates the row's ``status`` in place rather
    than emitting a compensating DEBIT — cf ``cashback_service.resolve_*``).
    Mirrors the FE 'savings to date' counter shown in the dashboard.
    """
    del window_days, extra
    from ratis_core.models.rewards import CashbackTransaction

    return _sum_for_user(
        db,
        user_id,
        CashbackTransaction,
        CashbackTransaction.amount,
        CashbackTransaction.type == "CREDIT",
        CashbackTransaction.status.in_(("pending", "confirmed")),
    )


def _eval_savings_eur_total(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_savings_eur_total``."""
    return _compute_savings_eur_total(db, user_id, window_days, extra) >= int(target)


def _compute_streak_days(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Current ``user_streaks.current_streak_days`` value (Feed Jack denorm).

    No row → 0 (the row is materialised lazily on the user's first feed event).
    """
    del window_days, extra
    return _max_streak_for_user(db, user_id)


def _eval_streak_days(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_streak_days``."""
    return _compute_streak_days(db, user_id, window_days, extra) >= int(target)


def _compute_referral_count(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Count gift_card_orders with ``source_type='referral_reward'`` AND
    ``eligible_at IS NOT NULL`` (= 30-day anti-churn delay started) for the
    referrer.
    """
    del window_days, extra
    from ratis_core.models.rewards import GiftCardOrder

    return _count_for_user(
        db,
        user_id,
        GiftCardOrder,
        GiftCardOrder.source_type == "referral_reward",
        GiftCardOrder.eligible_at.isnot(None),
    )


def _eval_referral_count(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_referral_count``."""
    return _compute_referral_count(db, user_id, window_days, extra) >= int(target)


def _compute_unique_brands_count(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Count distinct ``store_id`` for the user's accepted/matched scans.

    NULL ``store_id`` (label scans with ``store_status='unknown'``) excluded.
    """
    del window_days, extra
    from ratis_core.models.scan import Scan

    base = select(Scan).where(
        Scan.user_id == user_id,
        Scan.status.in_(_ACCEPTED_SCAN_STATUSES),
        Scan.store_id.isnot(None),
    )
    return _count_distinct_for_user(db, user_id, Scan.store_id, base)


def _eval_unique_brands_count(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_unique_brands_count``."""
    return _compute_unique_brands_count(db, user_id, window_days, extra) >= int(target)


def _compute_unique_categories_count(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Count distinct ``products.category_id`` for the user's accepted/matched
    scans (joined on ``scans.product_ean`` → ``products.ean``).

    The JOIN means we cannot use the bare ``_count_for_user`` primitive — we
    pass a fully-built SELECT into ``_count_distinct_for_user`` instead.
    """
    del window_days, extra
    from ratis_core.models.product import Product
    from ratis_core.models.scan import Scan

    base = (
        select(Scan)
        .select_from(Scan)
        .join(Product, Scan.product_ean == Product.ean)
        .where(
            Scan.user_id == user_id,
            Scan.status.in_(_ACCEPTED_SCAN_STATUSES),
            Product.category_id.isnot(None),
        )
    )
    return _count_distinct_for_user(db, user_id, Product.category_id, base)


def _eval_unique_categories_count(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_unique_categories_count``."""
    return _compute_unique_categories_count(db, user_id, window_days, extra) >= int(target)


def _compute_unique_products_discovered_count(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Count products this user was the first on Ratis to scan.

    Activated in V1.1 (KP-75 / migration ``20260510_2100_pfd``) — the
    ``products.first_discovered_by_user_id`` column is populated
    atomically by ``ratis_core.products.claim_first_discovery`` on every
    accepted/matched scan (receipt + label + barcode rescue + admin
    override paths). Counting first-discoveries is O(1)-per-row via the
    partial index ``idx_products_first_discovered``.

    The handler powers the seed achievement ``exp_unknown_10``
    (Pionnier·e — Découvrir 10 produits jamais vus, Émeraude / 150 CAB).

    Note : ``Product`` does not have its own ``user_id`` column — discovery
    attribution lives in ``first_discovered_by_user_id``. We bypass the
    generic ``_count_for_user`` primitive (which assumes ``model.user_id``)
    and inline the COUNT here.
    """
    del window_days, extra
    from ratis_core.models.product import Product

    return (
        db.scalar(select(func.count()).select_from(Product).where(Product.first_discovered_by_user_id == user_id)) or 0
    )


def _eval_unique_products_discovered_count(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_unique_products_discovered_count``."""
    return _compute_unique_products_discovered_count(db, user_id, window_days, extra) >= int(target)


def _compute_first_event(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Discrete progress for a ``first_event`` trigger : 0 (not seen yet) or 1.

    Since ``_first_event_seen`` is True only when the user has actually
    triggered the event (the SQL filter at candidate selection guarantees
    discrimination by event_type ; the ``NOT EXISTS user_achievements``
    clause guarantees novelty), the live progress for an *unlocked* user is
    1 ; for a *not-yet-unlocked* user it is 0 — the standard X/Y bar shows
    "0/1" until the unlock fires. The serializer caps at target=1 so the
    bar fills entirely on unlock.

    For non-unlocked users we return 0 (the only meaningful pre-unlock
    progress) — the dispatcher never invokes this computer once the user
    has unlocked the achievement (``NOT EXISTS`` filter), so no risk of
    a stale 0 overriding the unlocked state.
    """
    del db, user_id, window_days, extra
    return 0


def _eval_first_event(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Always True — discrimination by event_type is enforced at the SQL
    candidate-selection layer (``Achievement.extra_params['event'].astext ==
    event_type`` filter in ``check_achievements``).

    If we are called, the candidate already matched the current event by code
    path : the only remaining work is to confirm "this user has not unlocked
    it before", which the dispatcher's ``NOT EXISTS user_achievements`` clause
    has also already done. Hence True.

    Implementation delegates to the ``_first_event_seen`` primitive — kept
    that way so the handler ↔ primitive mapping is uniform across all 9
    triggers. The ``extra.get('event')`` value is forwarded to the primitive
    for parity with future implementations (e.g. event-log lookup).
    """
    del target, window_days
    return _first_event_seen(db, user_id, extra.get("event", ""))


def _compute_savings_eur_in_window(
    db: Session,
    user_id: UUID,
    window_days: int | None,
    extra: dict,
) -> int:
    """Sum CREDIT cashback (cents) within the trailing ``window_days`` window.

    BATCH-ONLY — exposed for progress-display parity but the handler
    (``_eval_savings_eur_in_window``) is registered in
    ``_BATCH_ONLY_HANDLERS``, NOT in ``TRIGGER_HANDLERS``.

    ``window_days=None`` → 0 (no point computing an unbounded window
    here ; the unbounded version is ``_compute_savings_eur_total``).

    Status filter mirrors ``_compute_savings_eur_total`` — ``refused`` rows
    excluded explicitly (in-place status update, no compensating DEBIT row).
    """
    del extra
    from ratis_core.models.rewards import CashbackTransaction

    if not window_days:
        return 0
    since = datetime.now(UTC) - timedelta(days=window_days)
    return _sum_for_user(
        db,
        user_id,
        CashbackTransaction,
        CashbackTransaction.amount,
        CashbackTransaction.type == "CREDIT",
        CashbackTransaction.status.in_(("pending", "confirmed")),
        CashbackTransaction.created_at >= since,
    )


def _eval_savings_eur_in_window(
    db: Session,
    user_id: UUID,
    target: float,
    window_days: int | None,
    extra: dict,
) -> bool:
    """Threshold wrapper — delegates to ``_compute_savings_eur_in_window``.

    BATCH-ONLY — not registered in ``TRIGGER_HANDLERS``, only in
    ``_BATCH_ONLY_HANDLERS`` (consumed by ``ratis_batch_achievements``).
    """
    if not window_days:
        return False
    return _compute_savings_eur_in_window(db, user_id, window_days, extra) >= int(target)


# ---------------------------------------------------------------------------
# Trigger registries — populated at module import after handlers are defined.
# ---------------------------------------------------------------------------
TRIGGER_HANDLERS: dict[str, HandlerFn] = {
    "scan_count": _eval_scan_count,
    "savings_eur_total": _eval_savings_eur_total,
    "streak_days": _eval_streak_days,
    "referral_count": _eval_referral_count,
    "unique_brands_count": _eval_unique_brands_count,
    "unique_categories_count": _eval_unique_categories_count,
    "unique_products_discovered_count": _eval_unique_products_discovered_count,
    "first_event": _eval_first_event,
}

_BATCH_ONLY_HANDLERS: dict[str, HandlerFn] = {
    "savings_eur_in_window": _eval_savings_eur_in_window,
}


# V1.1 — TRIGGER_PROGRESS_COMPUTERS dispatches a trigger_type to its
# ``_compute_*`` (returns the live scalar value, not a bool). The registry
# is used by ``compute_progress`` (called by the serializer to surface the
# X/Y bar in the FE — KP-76 fix). All triggers are mapped here, *including*
# the batch-only ``savings_eur_in_window`` — the FE wants progress for it
# regardless of the unlock path.
TRIGGER_PROGRESS_COMPUTERS: dict[str, ComputerFn] = {
    "scan_count": _compute_scan_count,
    "savings_eur_total": _compute_savings_eur_total,
    "streak_days": _compute_streak_days,
    "referral_count": _compute_referral_count,
    "unique_brands_count": _compute_unique_brands_count,
    "unique_categories_count": _compute_unique_categories_count,
    "unique_products_discovered_count": _compute_unique_products_discovered_count,
    "first_event": _compute_first_event,
    "savings_eur_in_window": _compute_savings_eur_in_window,
}


# ---------------------------------------------------------------------------
# Progress computer (V1.1) — KP-76 fix
# ---------------------------------------------------------------------------


def compute_progress(
    db: Session,
    ach: Achievement,
    user_id: UUID,
) -> int | float | None:
    """Live progress value for ``ach`` from ``user_id``'s POV, capped at target.

    Returns :

    * ``int`` (or ``float`` from the cap clamp on a Numeric target) — the
      current scalar value, capped at ``ach.target_value`` so the FE bar
      shows ``47/50``, never ``124/50``.
    * ``None`` when the trigger has no registered computer (forward-compat
      for new trigger types added by an admin without code change), OR
      when the underlying SQL raises (defensive fallback — the FE handles
      ``null`` gracefully).

    Wired to the serializer in ``serialize_achievement_for_user`` for
    NOT-yet-unlocked achievements only ; unlocked achievements report the
    catalog target as their progress (handled in the serializer itself).

    ⚠️ N+1 caveat (V1.1) : ``GET /rewards/achievements`` calls this for
    every non-unlocked achievement → 1 SELECT per row. Acceptable at the
    current 23-entry catalog (total ~150 ms per scroll). V2 should batch
    by ``trigger_type`` (1 SELECT per type, fan out to all rows).
    """
    computer = TRIGGER_PROGRESS_COMPUTERS.get(ach.trigger_type)
    if computer is None:
        return None
    try:
        value = computer(db, user_id, ach.window_days, ach.extra_params or {})
    except Exception:
        logger.exception(
            "achievement_compute_progress_failed",
            extra={"achievement_code": ach.code, "user_id": str(user_id)},
        )
        return None
    return min(value, int(ach.target_value))


# ---------------------------------------------------------------------------
# Unlock — Task 2.4
# ---------------------------------------------------------------------------

# Hard cap on the JSONB ``trigger_event`` column to prevent oversize blobs from
# poisoning ``user_achievements`` rows. Anything bigger is replaced with a stub
# row that records the original size for debugging.
_TRIGGER_EVENT_MAX_BYTES = 2048


def _truncate_jsonb(payload: dict[str, Any] | None, max_bytes: int) -> dict[str, Any] | None:
    if not payload:
        return None
    raw = json.dumps(payload, default=str)
    encoded = raw.encode("utf-8")
    if len(encoded) <= max_bytes:
        return payload
    return {"_truncated": True, "_original_size_bytes": len(encoded)}


def _unlock(
    db: Session,
    user_id: UUID,
    ach: Achievement,
    trigger_event: dict[str, Any] | None,
) -> bool:
    """Insert ``user_achievements`` row + grant CAB transactionally.

    Idempotent via UNIQUE(user_id, achievement_id) + ``ON CONFLICT DO NOTHING`` :
    concurrent unlock attempts (two parallel events, batch + event collision)
    silently no-op on the loser side without doubling the CAB grant.

    Returns ``True`` if newly unlocked, ``False`` if the row already existed
    (race lost or replay).

    Notes :

    * ``cab_granted`` snapshots ``ach.cab_reward`` at unlock time. Future
      grille re-prices won't rewrite history.
    * The CAB credit goes through ``award_cab`` (the existing canonical
      credit helper — see ``repositories/cab_repository.py``) with
      ``apply_streak_multiplier=False`` and ``apply_to_bp_progress=False``.
      The catalog prize is the source-of-truth ; multipliers and BP feeders
      would muddy the rarity-based grille.
    * **Commit contract** — ``_unlock`` does NOT commit. It leaves the
      ``user_achievements`` INSERT + ``cabecoin_transactions`` row (and the
      ``user_cab_balance`` UPDATE inside ``award_cab``) pending on the
      caller's session. The caller is responsible for committing — either
      via a ``with db_transaction(db):`` wrapper (the route pattern) or an
      explicit ``db.commit()`` (admin-grant / secret-event endpoints).
      This was changed in PR #388 to fix audit finding F-RW-1 : the
      previous behaviour committed the caller's pending writes mid-flow,
      breaking the transactional contract of the dispatch site.
    """
    # Local import to avoid the ratis_rewards ↔ ratis_core circular at module
    # load (cab_repository imports from ratis_core, which is fine, but the
    # service ought to keep its surface narrow).
    from repositories.cab_repository import award_cab

    stmt = (
        pg_insert(UserAchievement)
        .values(
            user_id=user_id,
            achievement_id=ach.id,
            cab_granted=ach.cab_reward,
            trigger_event=_truncate_jsonb(trigger_event, _TRIGGER_EVENT_MAX_BYTES),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "achievement_id"])
        .returning(UserAchievement.id)
    )
    inserted_id = db.scalar(stmt)
    if inserted_id is None:
        # Race lost OR replay : the row was already there. No CAB grant.
        return False

    if ach.cab_reward > 0:
        award_cab(
            db,
            user_id,
            int(ach.cab_reward),
            "achievement_unlock",
            reference_id=ach.id,
            reference_type="achievement",
            apply_streak_multiplier=False,
            apply_to_bp_progress=False,
        )
    # NB: NO db.commit() here — see "Commit contract" in the docstring.
    # The caller's ``with db_transaction(db):`` (or explicit commit) is
    # responsible for persisting the unlock + CAB grant atomically with
    # whatever upstream writes triggered the dispatch.

    # Fire-and-forget notification — the unlock and CAB grant are pending
    # on the caller's session ; the caller will commit shortly. If the
    # caller rolls back instead, the notification is a false positive,
    # but the notif helper is already best-effort (no DB write) so this
    # is acceptable. ``notify_achievement_unlocked`` is itself wrapped in
    # a try/except (fire-and-forget contract), but we keep this defensive
    # second wrap so an unexpected ImportError or kwarg mismatch can never
    # poison the unlock path either.
    try:
        from services import achievement_notification_service

        achievement_notification_service.notify_achievement_unlocked(user_id, ach)
    except Exception:
        logger.exception(
            "achievement_notify_dispatch_failed",
            extra={"achievement_code": ach.code, "user_id": str(user_id)},
        )
    return True


# ---------------------------------------------------------------------------
# Dispatcher — Task 2.5
# ---------------------------------------------------------------------------


def check_achievements(
    db: Session,
    user_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> list[UUID]:
    """Event-driven achievement dispatcher.

    Called fire-and-forget by the upstream event source (cab_service,
    cashback_service, streak_service, referral_service, battlepass_service)
    INSIDE the source's open transaction (typically the route's
    ``with db_transaction(db):`` block). Unlocks and CAB grants are left
    pending on the session ; the wrapping transaction will commit them
    atomically with the upstream writes that triggered the dispatch.
    Exceptions are caught and reported to Sentry — never propagated.

    See ``_unlock`` § "Commit contract" for the rationale (audit F-RW-1).

    Returns the list of newly-unlocked Achievement IDs (empty if nothing
    triggered, the user is banned/deleted, or every candidate handler raised).

    Skip order :

    1. Anti-shadow-ban / anti-deleted : silent skip, return ``[]``.
    2. Empty trigger map for ``event_type`` : skip (unknown event).
    3. Filter out windowed triggers — those are batch-only.
    4. SELECT candidates : (a) trigger_type matches, (b) availability window
       OK, (c) not already unlocked, (d) for ``first_event``, the
       ``extra_params.event`` JSON field equals ``event_type`` (CRITICAL
       discrimination — without this, every ``first_event`` row would unlock
       on every event of any type).
    5. For each candidate : run handler ; on True, run ``_unlock`` ; collect
       the IDs of those actually inserted (idempotent — race losers don't
       count).

    Per-candidate exceptions are isolated : one buggy handler does not poison
    the rest of the batch.
    """
    if payload is None:
        payload = {}

    user = db.get(User, user_id)
    if user is None or user.is_shadow_banned or user.is_deleted:
        return []

    trigger_types = EVENT_TYPE_TO_TRIGGERS.get(event_type, set()) - WINDOWED_TRIGGER_TYPES
    if not trigger_types:
        return []

    now = datetime.now(UTC)
    candidates = db.scalars(
        select(Achievement).where(
            Achievement.trigger_type.in_(trigger_types),
            or_(
                Achievement.available_from.is_(None),
                Achievement.available_from <= now,
            ),
            or_(
                Achievement.available_until.is_(None),
                Achievement.available_until > now,
            ),
            ~exists().where(
                and_(
                    UserAchievement.user_id == user_id,
                    UserAchievement.achievement_id == Achievement.id,
                )
            ),
            # CRITICAL — first_event achievements MUST discriminate by the
            # ``extra_params.event`` JSON value or they all match every event.
            # Non-first_event triggers always pass this OR clause.
            or_(
                Achievement.trigger_type != "first_event",
                Achievement.extra_params["event"].astext == event_type,
            ),
        )
    ).all()

    unlocked: list[UUID] = []
    for ach in candidates:
        handler = TRIGGER_HANDLERS.get(ach.trigger_type)
        if handler is None:
            logger.warning(
                "achievement_no_handler",
                extra={
                    "trigger_type": ach.trigger_type,
                    "achievement_code": ach.code,
                },
            )
            continue
        # SAVEPOINT-per-iteration : a single buggy handler / failing
        # ``_unlock`` rolls back ONLY its own partial writes. Previous
        # successful unlocks in the same dispatch loop AND the caller's
        # upstream writes (which we never see) remain intact. This
        # replaces the pre-F-RW-1 behaviour where ``_unlock`` committed
        # mid-iteration and a subsequent failure called ``db.rollback()``
        # on the (now-empty) outer txn.
        nested = db.begin_nested()
        try:
            if handler(
                db,
                user_id,
                float(ach.target_value),
                ach.window_days,
                ach.extra_params or {},
            ) and _unlock(db, user_id, ach, payload):
                unlocked.append(ach.id)
            nested.commit()
        except Exception:
            nested.rollback()
            logger.exception(
                "achievement_check_failed",
                extra={
                    "achievement_code": ach.code,
                    "trigger_type": ach.trigger_type,
                    "event_type": event_type,
                },
            )
            sentry_sdk.capture_exception()
    return unlocked
