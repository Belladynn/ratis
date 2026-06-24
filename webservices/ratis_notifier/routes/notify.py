from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_internal_key
from services.notify_service import get_now, get_rate_limiter, send_notification
from services.push_rate_limiter import PushRateLimiter
from sqlalchemy.orm import Session

router = APIRouter()


def get_cfg(request: Request) -> dict:
    return request.app.state.cfg


NotifType = Literal[
    "scan_done",
    "cashback_available",
    "badge_unlocked",
    "price_alert",
    # ratis_list_optimiser — emitted via legacy ``notify_user()`` once
    # ``run_optimize_route`` finishes (cf optimization_service.py).
    "route_ready",
    "battlepass_milestone_unlocked",
    "challenge_milestone_unlocked",
    "mystery_product_found",
    "store_validated",
    # ratis_batch_data_reconciliation Job 4 — gratitude push when scans
    # are retroactively resolved overnight (Bloc I NRC). Aggregated per
    # user (one notif covers N scans), payload carries scans_count + cab_total.
    "retro_cab_gratitude",
    # Achievements V1 (PR3) — unlock notification with rarity-gradated UX.
    # Payload carries reserved keys ``_visible_push`` /
    # ``_push_rate_limit_seconds`` / ``_push_title`` / ``_push_body``
    # injected by ``ratis_core.notifier_client.send`` for downstream parsing
    # by the notifier service. Server-side rate-limit enforcement is V1.1.
    "achievement_unlocked",
    # ratis_batch_trust_score — anti-fraud V1 visible warning push sent
    # when a user crosses the warn threshold but is not (yet) shadow-banned.
    # Payload : ``{trust_score: int}``. Shadow-banned users are silent.
    "trust_score_warning",
]


class NotifyRequest(BaseModel):
    user_id: uuid.UUID
    notif_type: NotifType = Field(alias="type")  # alias: "type" is a Python builtin
    data: dict[str, Any] = {}


# verify_internal_key in dependencies=[] — returns None, used only for the 403 side-effect.
@router.post("/notify", status_code=202, dependencies=[Depends(verify_internal_key)])
def notify(
    body: NotifyRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    now_factory: Callable[[], datetime] = Depends(get_now),
    cfg: dict = Depends(get_cfg),
    rate_limiter: PushRateLimiter = Depends(get_rate_limiter),
) -> dict:
    """
    Enqueue a push notification for a user.

    Always returns 202 — fire and forget. Errors are logged internally
    and never surfaced to the caller.
    """
    now: datetime = now_factory()

    background_tasks.add_task(
        send_notification,
        db=db,
        user_id=body.user_id,
        notif_type=body.notif_type,
        data=body.data,
        cfg=cfg,
        now=now,
        rate_limiter=rate_limiter,
    )
    return {"status": "queued"}
