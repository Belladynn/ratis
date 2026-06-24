"""User-facing Achievement endpoints.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § 7
"Endpoints API + admin + frontend integration".

* ``GET  /rewards/achievements``                  — list catalog grouped by
  category (8 buckets — the 7 catalog categories + the computed
  ``j_y_etais``). Filters ``?category=…`` and ``?unlocked=true|false``.
* ``GET  /rewards/achievements/{achievement_id}`` — single achievement
  detail. Honours hidden + secret + limited-time visibility rules.
* ``POST /rewards/achievements/secret-event``     — fire a secret event
  (``konami_code_entered`` / ``app_opened_at_3am``) which the dispatcher
  may turn into an unlock. Rate-limited 10/h/user.

Display-rule logic lives in ``services/achievement_serializer.py`` —
this module is a thin HTTP wrapper that loads the catalog + user
unlocks and feeds them through the serializer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from db_utils import db_transaction
from deps import get_current_user
from fastapi import APIRouter, Depends, Query, Request
from limiter import limiter
from pydantic import BaseModel
from ratis_core.database import get_db
from ratis_core.exceptions import NotFound
from ratis_core.models.achievement import Achievement, UserAchievement
from ratis_core.models.user import User
from services.achievement_serializer import serialize_achievement_for_user
from services.achievement_service import check_achievements
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Category labels — computed display-only, lives at the API layer (not in
# the DB) so renames don't require a migration. The 8th bucket
# ``j_y_etais`` exists only at the API level (cf serializer Rule 4).
# ---------------------------------------------------------------------------
CATEGORY_LABELS: dict[str, str] = {
    "volume": "Scans",
    "savings": "Économies",
    "streak": "Régularité",
    "social": "Social",
    "exploration": "Exploration",
    "seasonal": "Saisonniers",
    "secret": "Secrets",  # pragma: allowlist secret
    "j_y_etais": "J'y étais",  # pragma: allowlist secret
}

# Display order of the 8 buckets — keep ``j_y_etais`` last so it stays
# visually below the regular categories on the FE list.
_CATEGORY_ORDER: tuple[str, ...] = (
    "volume",
    "savings",
    "streak",
    "social",
    "exploration",
    "seasonal",
    "secret",
    "j_y_etais",
)


# ===========================================================================
# GET /rewards/achievements
# ===========================================================================


@router.get("/rewards/achievements")
def list_achievements(
    category: str | None = Query(default=None),
    unlocked: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the full achievement catalog grouped by category, with
    per-user unlock state.

    * ``category=<key>`` restricts the response to a single bucket.
      Filtering on ``j_y_etais`` returns the user's closed-window
      unlocks specifically (computed-only — never present in the
      catalog ``category`` column).
    * ``unlocked=true`` returns only achievements this user has
      unlocked (across all buckets).
    * ``unlocked=false`` returns only achievements this user has NOT
      unlocked yet (and which are visible — ``is_hidden=False`` or
      already unlocked, ``is_secret=False`` masked otherwise).

    The serializer ``serialize_achievement_for_user`` enforces secret /
    hidden / limited-time / j_y_etais override rules — the route only
    assembles, filters and sorts.
    """
    now = datetime.now(UTC)

    # 1) Load all catalog rows + user unlocks in 2 queries (no N+1).
    catalog: list[Achievement] = list(db.scalars(select(Achievement).order_by(Achievement.display_order)).all())
    user_unlocks: dict[uuid.UUID, UserAchievement] = {
        ua.achievement_id: ua
        for ua in db.scalars(select(UserAchievement).where(UserAchievement.user_id == current_user.id)).all()
    }

    # 2) Serialise each row from the user's POV, dropping ``None`` (hidden
    #    or closed-window-not-unlocked). ``db`` + ``user_id`` are forwarded
    #    so the serializer can populate the V1.1 ``progress`` field via
    #    ``achievement_service.compute_progress`` (KP-76 fix). N+1 caveat :
    #    ~1 SELECT per non-unlocked achievement (~150 ms on the V1 23-row
    #    catalog) — acceptable today, V2 should batch by trigger_type.
    by_category: dict[str, list[dict]] = {key: [] for key in _CATEGORY_ORDER}
    for ach in catalog:
        ua = user_unlocks.get(ach.id)
        item = serialize_achievement_for_user(ach, ua=ua, now=now, db=db, user_id=current_user.id)
        if item is None:
            continue
        # 3) Apply ``unlocked`` filter at the item level.
        if unlocked is True and not item["unlocked"]:
            continue
        if unlocked is False and item["unlocked"]:
            continue
        # 4) Apply ``category`` filter on the SERIALIZED category (so
        #    ``?category=j_y_etais`` correctly catches override rows).
        if category is not None and item["category"] != category:
            continue
        by_category[item["category"]].append(item)

    # 5) Build response — drop empty buckets, preserve declared order.
    categories = [
        {
            "key": key,
            "label": CATEGORY_LABELS[key],
            "items": by_category[key],
        }
        for key in _CATEGORY_ORDER
        if by_category[key]
    ]
    return {"categories": categories}


# ===========================================================================
# GET /rewards/achievements/{achievement_id}
# ===========================================================================


@router.get("/rewards/achievements/{achievement_id}")
def get_achievement_detail(
    achievement_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Single achievement detail. 404 when unknown OR when the user
    must not see it (hidden + not unlocked, closed window + not
    unlocked).
    """
    ach = db.get(Achievement, achievement_id)
    if ach is None:
        raise NotFound("achievement_not_found")

    ua = db.scalar(
        select(UserAchievement).where(
            UserAchievement.user_id == current_user.id,
            UserAchievement.achievement_id == achievement_id,
        )
    )
    out = serialize_achievement_for_user(
        ach,
        ua=ua,
        now=datetime.now(UTC),
        db=db,
        user_id=current_user.id,
    )
    if out is None:
        # Hidden + not unlocked OR closed window + not unlocked — same
        # treatment as the listing : the achievement is not discoverable
        # so we deny knowledge of its existence.
        raise NotFound("achievement_not_found")
    return out


# ===========================================================================
# POST /rewards/achievements/secret-event
# ===========================================================================


class SecretEventRequest(BaseModel):
    """Payload for the secret-event endpoint.

    ``Literal`` enforces the whitelist at the Pydantic layer — any other
    value returns 422 before the handler runs and before the rate
    counter is decremented.
    """

    event: Literal["konami_code_entered", "app_opened_at_3am"]


@router.post("/rewards/achievements/secret-event", status_code=200)
@limiter.limit("10/hour")
def post_secret_event(
    request: Request,
    body: SecretEventRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Forward the secret event to the achievement dispatcher.

    The dispatcher itself is fire-and-forget (cf
    ``services/achievement_service.py``) — it never raises back to us.
    Since F-RW-1, ``_unlock`` does NOT commit on its own ; this route
    is the transaction owner and must commit explicitly so the unlock
    + CAB grant land. We surface only the count so the FE can pop a
    "+CAB" toast without a second round trip.

    Rate-limited per-user (10/h) — the secret events are intentional
    easter eggs ; no legitimate UX requires bursts.
    """
    with db_transaction(db):
        unlocked = check_achievements(
            db,
            current_user.id,
            body.event,
            {"source": "user_event", "event": body.event},
        )
    return {"ok": True, "unlocked_count": len(unlocked)}
