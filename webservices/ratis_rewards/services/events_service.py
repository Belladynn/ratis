"""
Generic gamification event handler — Phase B (PR #325).

``handle_action`` is the single orchestrator for every event emitted via
``trigger_action``. It :

1. Inserts (or finds an existing) ``reward_events`` row keyed on
   ``idempotency_key``. A duplicate inserts a second row marked
   ``status='duplicate'`` for forensics and returns early — CAB, XP and
   mission progress are awarded exactly once per key.
2. Maps the ``action_type`` to its CAB / XP base amounts (settings).
3. Awards CAB / XP scaled by ``quantity``.
4. Progresses every active mission whose ``(action_type, qualifier)``
   tuple matches the event. ``scan_distinct`` missions deduplicate via
   ``user_missions.tracked_values``.
5. Marks the ``reward_events`` row ``status='processed'`` and commits
   (commit owned by the caller — the route uses ``db_transaction``).

Errors raised by side-effect helpers bubble up — the caller's
``db_transaction`` rolls back and the ``reward_events`` row is rolled
back along with it. We deliberately do **not** persist a ``failed`` row
on the rollback path : the next retry should reach the same code with
the same idempotency_key and observe a fresh insert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, date, datetime
from typing import Any

from repositories.battlepass_repository import get_newly_unlocked_bp_milestones
from repositories.cab_repository import (
    award_cab,
    get_active_season,
    get_cab_earned_season,
)
from repositories.challenge_repository import (
    get_active_community_multipliers,
    maybe_increment_challenge,
)
from repositories.missions_repository import check_missions_progress
from repositories.notification_repository import enqueue_notification
from repositories.xp_repository import award_xp
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Action types whose CAB / XP base lives at ``settings["rewards"]["cab_per_<action_type>"]``
# / ``settings["xp"]["xp_per_<action_type>"]``. Each entry must have a row in
# ratis_settings.json — missing keys = settings.* lookup raises KeyError.
_KNOWN_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "receipt_scan",
        "label_scan",
        "product_identification",
        "fill_product_field",
        "scan_distinct",
        "promo_found",
        "price_compared",
    }
)


def _is_shadow_banned(db: Session, user_id: uuid.UUID) -> bool:
    """Return True if the user is shadow-banned (anti-fraud V1).

    Shadow-banned users do not earn CAB / XP / mission progress — silent
    skip, no error surfaced. See ``ARCH_anti_fraud.md`` § "Effets du
    shadow ban" for the full effect set.
    """
    row = db.execute(
        text("SELECT is_shadow_banned FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    return bool(row and row.is_shadow_banned)


def _synth_idempotency_key(
    user_id: uuid.UUID,
    action_type: str,
    qualifier: str | None,
    context: dict | None,
) -> str:
    """Build a deterministic key when the caller did not provide one.

    Includes the truncated minute timestamp + (optional) scan_id so two
    distinct events from the same user are not collapsed by accident.
    """
    scan_ref = ""
    if context:
        scan_ref = str(context.get("scan_id") or context.get("ean") or "")
    minute = int(time.time() // 60)
    raw = f"{user_id}|{action_type}|{qualifier or ''}|{scan_ref}|{minute}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _insert_event_row(
    db: Session,
    *,
    user_id: uuid.UUID,
    action_type: str,
    qualifier: str | None,
    quantity: int,
    idempotency_key: str,
    context: dict | None,
) -> tuple[uuid.UUID | None, bool]:
    """Insert a fresh reward_events row.

    Returns ``(event_id, is_duplicate)`` where ``event_id`` is the
    primary key of the freshly-inserted row, or None on conflict. On
    conflict (= the idempotency_key already exists) we INSERT a second
    "duplicate" log row so the audit trail records both attempts.
    """
    payload_json = json.dumps(context) if context else None
    fresh_id = uuid.uuid4()
    res = db.execute(
        text(
            "INSERT INTO reward_events "
            "  (id, user_id, action_type, qualifier, quantity, "
            "   idempotency_key, status, payload) "
            "VALUES (:id, :uid, :action, :qualifier, :quantity, "
            "        :ikey, 'pending', CAST(:payload AS jsonb)) "
            "ON CONFLICT (idempotency_key) DO NOTHING "
            "RETURNING id"
        ),
        {
            "id": fresh_id,
            "uid": user_id,
            "action": action_type,
            "qualifier": qualifier,
            "quantity": quantity,
            "ikey": idempotency_key,
            "payload": payload_json,
        },
    )
    row = res.first()
    if row is not None:
        return row.id, False

    # Duplicate path : the caller already processed an event with this
    # key. We log a second row marked ``duplicate`` for forensics.
    # Different idempotency_key would violate the UNIQUE constraint, so
    # we synthesise a one-shot value (sha256 of the original key + a
    # uuid). The duplicate row is a *log*, not a dedup primitive.
    dup_key = hashlib.sha256(f"{idempotency_key}|dup|{uuid.uuid4()}".encode()).hexdigest()
    db.execute(
        text(
            "INSERT INTO reward_events "
            "  (id, user_id, action_type, qualifier, quantity, "
            "   idempotency_key, status, payload, processed_at) "
            "VALUES (:id, :uid, :action, :qualifier, :quantity, "
            "        :ikey, 'duplicate', CAST(:payload AS jsonb), now())"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "action": action_type,
            "qualifier": qualifier,
            "quantity": quantity,
            "ikey": dup_key,
            "payload": payload_json,
        },
    )
    return None, True


def _mark_processed(db: Session, event_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE reward_events SET status='processed', processed_at=now() WHERE id = :id"),
        {"id": event_id},
    )


def _count_today_actions(
    db: Session,
    user_id: uuid.UUID,
    action_type: str,
    today: date,
    *,
    exclude_event_id: uuid.UUID,
) -> int:
    """Sum the units of today's ``action_type`` events for the user.

    Used by the daily diminishing-returns rule. We sum ``reward_events
    .quantity`` (not a plain row COUNT) so a single event carrying
    ``quantity=N`` correctly counts as N actions. The current event's
    own row — already inserted as ``status='pending'`` before this call
    — is excluded via ``exclude_event_id``. ``duplicate`` log rows are
    excluded ; ``pending`` / ``processed`` rows from earlier events in
    the same day are counted. The day boundary is UTC midnight.
    """
    return int(
        db.execute(
            text(
                "SELECT COALESCE(SUM(quantity), 0) FROM reward_events "
                "WHERE user_id = :uid AND action_type = :action_type "
                "  AND status <> 'duplicate' "
                "  AND id <> :exclude_id "
                "  AND created_at >= :day_start"
            ),
            {
                "uid": user_id,
                "action_type": action_type,
                "exclude_id": exclude_event_id,
                "day_start": datetime.combine(today, datetime.min.time(), tzinfo=UTC),
            },
        ).scalar()
        or 0
    )


def _apply_diminishing_returns(
    *,
    cab_amount_base: int,
    cab_per_unit: int,
    quantity: int,
    actions_done_today: int,
    threshold_per_day: int,
    multiplier_after: float,
) -> int:
    """Halve (per config) the CAB earned beyond the daily threshold.

    ARCH_cab_economy § "Diminishing returns journaliers" : up to
    ``threshold_per_day`` actions/day earn full rate ; every action past
    it earns ``cab_per_unit × multiplier_after``. ``actions_done_today``
    is the count BEFORE this event. The fractional excess is rounded to
    the nearest integer cent.
    """
    units_under = max(0, min(quantity, threshold_per_day - actions_done_today))
    units_over = quantity - units_under
    if units_over <= 0:
        return cab_amount_base
    return units_under * cab_per_unit + round(units_over * cab_per_unit * multiplier_after)


def handle_action(
    db: Session,
    *,
    user_id: uuid.UUID,
    action_type: str,
    quantity: int = 1,
    qualifier: str | None = None,
    idempotency_key: str | None = None,
    context: dict | None = None,
    rewards_cfg: dict[str, Any],
    xp_cfg: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Process one gamification event end-to-end.

    Returns a dict mirroring the route response :
        {"event_id": str|None, "duplicate": bool, "status": str}
    """
    if today is None:
        today = datetime.now(UTC).date()

    if action_type not in _KNOWN_ACTION_TYPES:
        raise ValueError(f"Unknown action_type: {action_type!r}")

    if idempotency_key is None:
        idempotency_key = _synth_idempotency_key(user_id, action_type, qualifier, context)

    event_id, is_duplicate = _insert_event_row(
        db,
        user_id=user_id,
        action_type=action_type,
        qualifier=qualifier,
        quantity=quantity,
        idempotency_key=idempotency_key,
        context=context,
    )

    if is_duplicate:
        logger.info(
            "trigger_action duplicate : user=%s action=%s key=%s",
            user_id,
            action_type,
            idempotency_key,
        )
        return {"event_id": None, "duplicate": True, "status": "duplicate"}

    # _insert_event_row returns event_id=None iff is_duplicate=True (it yields
    # the fresh row id otherwise). Having returned on the duplicate branch
    # above, event_id is guaranteed non-None from here on.
    assert event_id is not None  # non-duplicate path → real inserted row id

    # Anti-fraud V1 — shadow-banned users earn nothing on events.
    # The reward_events row already exists for forensics ; we mark it
    # processed so audits know the event was consumed (no double-count
    # on retry). CAB / XP / mission progression are skipped.
    if _is_shadow_banned(db, user_id):
        logger.info(
            "trigger_action: shadow-banned user %s — event recorded but rewards skipped",
            user_id,
        )
        _mark_processed(db, event_id)
        return {
            "event_id": str(event_id),
            "duplicate": False,
            "status": "processed",
        }

    cab_key = f"cab_per_{action_type}"
    xp_key = f"xp_per_{action_type}"
    cab_per_unit = int(rewards_cfg.get(cab_key, 0))
    cab_amount_base = cab_per_unit * quantity
    xp_amount_base = int((xp_cfg or {}).get(xp_key, 0)) * quantity if xp_cfg is not None else 0

    # Daily diminishing returns (ARCH_cab_economy § "Diminishing returns
    # journaliers") — once the user passes the per-day threshold for this
    # action_type, the CAB on the excess is halved. Thresholds live in
    # ratis_settings.json (rewards.diminishing_returns) ; action_types
    # without an entry (receipt_scan, …) are unaffected.
    dr_cfg = rewards_cfg.get("diminishing_returns", {}).get(action_type)
    if dr_cfg and cab_amount_base > 0:
        cab_amount_base = _apply_diminishing_returns(
            cab_amount_base=cab_amount_base,
            cab_per_unit=cab_per_unit,
            quantity=quantity,
            actions_done_today=_count_today_actions(db, user_id, action_type, today, exclude_event_id=event_id),
            threshold_per_day=int(dr_cfg["threshold_per_day"]),
            multiplier_after=float(dr_cfg["multiplier_after"]),
        )

    # Pre-fetch community multipliers — single query covers both branches.
    cab_mult, xp_mult = get_active_community_multipliers(db, user_id)

    # Snapshot battlepass progress before awarding to detect
    # newly-unlocked milestones.
    season = get_active_season(db)
    cab_before = get_cab_earned_season(db, user_id, season["id"]) if season else 0

    # ------------------------------------------------------------------
    # Reference for CAB / XP — for ``receipt_scan`` / ``label_scan`` /
    # ``product_identification`` we still pass scan_id from the context
    # so the UNIQUE INDEX ``uq_cabtx_scan_credit`` (one credit per scan)
    # stays effective. For the new event types (fill_product_field,
    # scan_distinct, promo_found) there is no single scan to anchor on,
    # so we omit reference_id/type.
    # ------------------------------------------------------------------
    scan_id_str = (context or {}).get("scan_id") if context else None
    scan_uuid: uuid.UUID | None = None
    if scan_id_str:
        try:
            scan_uuid = uuid.UUID(str(scan_id_str))
        except (ValueError, TypeError):
            scan_uuid = None
    use_scan_ref = (
        action_type
        in {
            "receipt_scan",
            "label_scan",
            "product_identification",
        }
        and scan_uuid is not None
    )

    if cab_amount_base > 0:
        award_cab(
            db,
            user_id,
            cab_amount_base,
            action_type,
            reference_id=scan_uuid if use_scan_ref else None,
            reference_type="scan" if use_scan_ref else None,
            community_multiplier=cab_mult,
            # Season already fetched above — skip the redundant
            # battlepass_seasons WHERE is_active lookup (RW-10).
            active_season_id=season["id"] if season else None,
        )

    if xp_amount_base > 0:
        award_xp(
            db,
            user_id,
            xp_amount_base,
            action_type,
            reference_id=scan_uuid if use_scan_ref else None,
            reference_type="scan" if use_scan_ref else None,
            community_multiplier=xp_mult,
        )

    # Mission progression. The repository's check_missions_progress now
    # honours qualifier filtering and tracked_values for scan_distinct.
    check_missions_progress(
        db,
        user_id,
        action_type,
        today,
        increment=quantity,
        qualifier=qualifier,
    )

    # Community challenges only react to the legacy scan-class events.
    if action_type in {"receipt_scan", "label_scan", "product_identification"}:
        maybe_increment_challenge(db, user_id, action_type)

    # Battlepass milestone unlock notifications.
    if season:
        for m in get_newly_unlocked_bp_milestones(db, user_id, season["id"], cab_before):
            enqueue_notification(
                db,
                user_id,
                "battlepass_milestone_unlocked",
                {"milestone_number": m["milestone_number"]},
            )

    # Mystery product check — only meaningful for scan-class events that
    # carry a scan_id (the existing service hashes on scan_id).
    if use_scan_ref:
        from services.mystery_service import process_mystery_find

        process_mystery_find(db, user_id, scan_uuid)  # type: ignore[arg-type]

    _mark_processed(db, event_id)

    # Achievements V1 — fire-and-forget within the same transaction.
    # The legacy `cab_service.handle_scan_accepted` was removed in PR #325 ;
    # `handle_action` is now the single entry point for scan-class rewards,
    # so the `scan_accepted` event fires here for the three scan action_types
    # (receipt_scan / label_scan / product_identification). Other action_types
    # (fill_product_field, scan_distinct, …) do not currently map to any
    # event in EVENT_TYPE_TO_TRIGGERS — they will be added when the matching
    # achievement triggers ship.
    #
    # `apply_to_bp_progress=True` is the default for award_cab() in this path,
    # so the season progress UPSERT happened above. We additionally fire the
    # `battlepass_season_participated` event the FIRST time the user touches
    # the active season — detected via `cab_before == 0` (no row yet OR row
    # with cab_earned_season=0 prior to this event).
    if action_type in {"receipt_scan", "label_scan", "product_identification"}:
        try:
            from services import achievement_service

            achievement_service.check_achievements(
                db,
                user_id=user_id,
                event_type="scan_accepted",
                payload={"scan_id": str(scan_uuid)} if scan_uuid else {},
            )
        except Exception:
            logger.exception(
                "achievement_hook_scan_accepted_failed",
                extra={"user_id": str(user_id)},
            )

    if season and cab_amount_base > 0 and cab_before == 0:
        try:
            from services import achievement_service

            achievement_service.check_achievements(
                db,
                user_id=user_id,
                event_type="battlepass_season_participated",
                payload={
                    "season_id": str(season["id"]),
                    "event_id": str(event_id),
                },
            )
        except Exception:
            logger.exception(
                "achievement_hook_battlepass_season_participated_failed",
                extra={"user_id": str(user_id)},
            )

    return {
        "event_id": str(event_id),
        "duplicate": False,
        "status": "processed",
    }
