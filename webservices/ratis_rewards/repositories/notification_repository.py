"""
Outbox pattern — notification_outbox table.

enqueue_notification  — INSERT dans la même transaction que l'événement.
process_outbox_batch  — dépile N lignes non envoyées, appelle notify_user, marque sent_at.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ratis_core.notifier_client import notify_user
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def enqueue_notification(
    db: Session,
    user_id: uuid.UUID,
    notif_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Insert a notification row in the current transaction (fire-and-forget on the write path)."""
    db.execute(
        text("INSERT INTO notification_outbox (user_id, type, data) VALUES (:uid, :type, CAST(:data AS jsonb))"),
        {
            "uid": user_id,
            "type": notif_type,
            "data": json.dumps(data or {}),
        },
    )


def process_outbox_batch(db: Session, batch_size: int = 50) -> int:
    """
    Fetch up to batch_size unsent notifications with FOR UPDATE SKIP LOCKED,
    call notify_user for each, mark sent_at = now() on success.

    Returns the number of notifications successfully dispatched.
    Errors on individual rows are logged and skipped (row left unsent for retry).
    """
    rows = db.execute(
        text(
            "SELECT id, user_id, type, data "
            "FROM notification_outbox "
            "WHERE sent_at IS NULL "
            "ORDER BY created_at "
            "LIMIT :limit "
            "FOR UPDATE SKIP LOCKED"
        ),
        {"limit": batch_size},
    ).fetchall()

    dispatched = 0
    for row in rows:
        try:
            notify_user(row.user_id, row.type, row.data or {})
            db.execute(
                text("UPDATE notification_outbox SET sent_at = now() WHERE id = :id"),
                {"id": row.id},
            )
            dispatched += 1
        except Exception:
            logger.exception(
                "outbox: failed to dispatch notification %s (type=%s, user=%s)",
                row.id,
                row.type,
                row.user_id,
            )
    if dispatched:
        db.commit()
    return dispatched
