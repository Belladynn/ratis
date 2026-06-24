"""Achievement → API response shape.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md
§ 5 "Limited-time + j_y_etais" + § 7 "Endpoints API".

Takes an ORM ``Achievement`` row and the user's ``UserAchievement`` row
(or ``None`` if not unlocked), returns the JSON-friendly dict the public
``GET /rewards/achievements*`` endpoints emit.

Display rules (single source-of-truth) :

1. **Limited-time CLOSED + not unlocked** → ``None`` — the achievement is
   no longer obtainable, hide it entirely.
2. **``is_hidden=True`` + not unlocked** → ``None`` — keep it secret-secret.
3. **``is_secret=True`` + not unlocked** → masked dict (label="???",
   icon="❓", description="Mystère...", code=None, no numeric fields).
4. **Limited-time CLOSED + UNLOCKED** → full dict with
   ``category='j_y_etais'`` (the 8th computed display-only category — the
   catalog itself never stores this value, it is applied here at
   serialization time so user "I was there" achievements regroup in a
   dedicated bucket on the FE).
5. **Otherwise** → full dict with the catalog's ``category``.

The ``progress`` field is wired in V1.1 (KP-76 fix) :
* Unlocked → ``target_value`` (full bar).
* Not unlocked + ``db`` + ``user_id`` provided → live value from
  ``achievement_service.compute_progress`` (capped at target).
* Otherwise → ``None`` (FE tolerates null gracefully).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ratis_core.models.achievement import Achievement, UserAchievement
from sqlalchemy.orm import Session


def serialize_achievement_for_user(
    ach: Achievement,
    ua: UserAchievement | None,
    now: datetime,
    db: Session | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Return the API response dict for ``ach`` from ``user``'s POV, or None.

    Caller (``GET /rewards/achievements`` listing route) must filter out
    ``None`` returns before assembling the response — those represent
    achievements the user must not see (closed window without unlock, or
    hidden-without-unlock).

    The optional ``db`` + ``user_id`` parameters enable the V1.1 live
    ``progress`` field — when both are provided, the serializer dispatches
    to ``achievement_service.compute_progress`` to surface the X/Y bar value
    for not-yet-unlocked achievements (KP-76). If either is missing, the
    legacy V1 behaviour is preserved (``progress: null``) — useful for unit
    tests that exercise the display-rule logic without a DB.

    Time semantics (cf the catalog ``available_from``/``available_until``
    columns) :
    * ``available_from is None`` → window opened forever ago.
    * ``available_until is None`` → window never closes.
    * Window-open == "now is in [from, until)" (inclusive lower, exclusive
      upper — the closing tick belongs to the closed period).
    """
    is_window_closed = ach.available_until is not None and now >= ach.available_until
    is_window_open = (ach.available_from is None or ach.available_from <= now) and (
        ach.available_until is None or ach.available_until > now
    )

    # Rule 1 — closed window + never unlocked → hide entirely.
    if is_window_closed and ua is None:
        return None

    # Rule 2 — hidden + never unlocked → hide entirely.
    if ua is None and ach.is_hidden:
        return None

    # Rule 4 prep — closed window + unlocked → category override applies in
    # both the masked branch (irrelevant — secret never overlaps with
    # limited-time in the catalog) and the full branch.
    display_category = "j_y_etais" if (is_window_closed and ua is not None) else ach.category

    # Rule 3 — secret + never unlocked → masked dict (no numeric leak — the
    # FE must not even hint at how close the user is to discovering it).
    if ua is None and ach.is_secret:
        return {
            "id": str(ach.id),
            "code": None,
            "label": "???",
            "description": "Mystère...",
            "icon": "❓",
            "rarity": ach.rarity,
            "category": display_category,
            "cab_reward": None,
            "target_value": None,
            "progress": None,
            "unlocked": False,
            "unlocked_at": None,
            "window_open": is_window_open,
        }

    # ------------------------------------------------------------------
    # ``progress`` resolution (V1.1 KP-76)
    #
    # Unlocked → progress = target_value (bar full).
    # Not unlocked + DB context → live value via achievement_service
    #   (capped at target by ``compute_progress`` itself).
    # Otherwise → None (legacy V1 behaviour, FE tolerates null).
    # ------------------------------------------------------------------
    if ua is not None:
        progress: int | float | None = float(ach.target_value)
    elif db is not None and user_id is not None:
        # Local import avoids import cycle (achievement_service imports
        # from ratis_core.models.achievement, which the serializer also
        # imports — keeping the dependency one-way at module import).
        from services.achievement_service import compute_progress

        progress = compute_progress(db, ach, user_id)
    else:
        progress = None

    # Rule 4 + 5 — full dict.
    return {
        "id": str(ach.id),
        "code": ach.code,
        "label": ach.label,
        "description": ach.description,
        "icon": ach.icon,
        "rarity": ach.rarity,
        "category": display_category,
        "cab_reward": ach.cab_reward,
        "target_value": float(ach.target_value),
        "progress": progress,
        "unlocked": ua is not None,
        "unlocked_at": ua.unlocked_at.isoformat() if ua else None,
        "window_open": is_window_open,
    }
