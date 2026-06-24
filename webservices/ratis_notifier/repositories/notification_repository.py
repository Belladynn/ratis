from __future__ import annotations

import uuid
from datetime import datetime

from ratis_core.models.analytics import NotificationLog, UserPushToken
from ratis_core.models.notifications import PushReceiptTicket
from ratis_core.models.user import User
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session


def acquire_user_cap_lock(db: Session, user_id: uuid.UUID) -> None:
    """Acquire a per-user transaction-scoped advisory lock.

    Serialises the daily-cap count+insert for one user across concurrent
    notify requests : without it, two requests both read ``count`` below the
    cap and both proceed, exceeding the cap by +1. The lock is held until the
    transaction commits or rolls back (PG releases it automatically), so the
    second caller only reads the count after the first caller's log row is
    visible. Same pattern as ``gift_card_cap_service.reserve_gift_card_cap``.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"notif_cap:{user_id}"},
    )


def get_user_timezone(db: Session, user_id: uuid.UUID) -> str:
    """Return the user's IANA timezone, or 'Europe/Paris' if unknown."""
    tz = db.scalar(select(User.timezone).where(User.id == user_id))
    return tz or "Europe/Paris"


def get_tokens(db: Session, user_id: uuid.UUID) -> list[UserPushToken]:
    return list(db.scalars(select(UserPushToken).where(UserPushToken.user_id == user_id)))


def delete_token(db: Session, token_id: uuid.UUID) -> None:
    """Remove an invalidated push token. Caller must commit() to persist."""
    token = db.get(UserPushToken, token_id)
    if token:
        db.delete(token)
        db.flush()


def count_sent_today(db: Session, user_id: uuid.UUID, since: datetime) -> int:
    """Count 'sent' notifications since the given datetime (caller computes local midnight)."""
    return (
        db.scalar(
            select(func.count(NotificationLog.id)).where(
                NotificationLog.user_id == user_id,
                NotificationLog.status == "sent",
                NotificationLog.sent_at >= since,
            )
        )
        or 0
    )


def create_log(
    db: Session,
    user_id: uuid.UUID,
    notif_type: str,
    status: str,
    payload: dict | None,
    expo_ticket_id: str | None,
    sent_at: datetime,
) -> NotificationLog:
    log = NotificationLog(
        id=uuid.uuid4(),
        user_id=user_id,
        type=notif_type,
        status=status,
        payload=payload,
        expo_ticket_id=expo_ticket_id,
        sent_at=sent_at,
    )
    db.add(log)
    db.flush()
    return log


def create_receipt_ticket(
    db: Session,
    user_id: uuid.UUID,
    push_token: str,
    expo_ticket_id: str,
) -> PushReceiptTicket:
    """Persist an Expo push ticket so the receipt-polling batch can later
    fetch its delivery outcome. Caller must commit() to persist."""
    ticket = PushReceiptTicket(
        id=uuid.uuid4(),
        user_id=user_id,
        push_token=push_token,
        expo_ticket_id=expo_ticket_id,
    )
    db.add(ticket)
    db.flush()
    return ticket
