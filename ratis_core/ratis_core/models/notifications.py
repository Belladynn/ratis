"""NotificationOutbox — outbox pattern pour les notifications push.

Une ligne est insérée dans la même transaction que l'événement déclencheur.
Le worker asyncio de ratis_rewards dépile périodiquement avec FOR UPDATE SKIP LOCKED.

PushReceiptTicket — Expo push-receipt tracking. Une ligne par (envoi, token)
réussi ; le batch ``ratis_batch_push_receipts`` interroge Expo pour le statut
final et supprime les tokens morts (``DeviceNotRegistered``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)


class PushReceiptTicket(Base):
    """One row per (push send, token) — persists the Expo ticket so the
    ``ratis_batch_push_receipts`` batch can poll Expo's receipts endpoint
    and delete dead push tokens (``DeviceNotRegistered``).

    ``push_token`` is stored as the token string (not an FK) so cleanup is a
    direct lookup and the ticket row survives the token's deletion, keeping
    an audit trail. ``checked_at`` is set once the receipt has been polled.
    """

    __tablename__ = "push_receipt_tickets"
    __table_args__ = (UniqueConstraint("expo_ticket_id", name="uq_push_receipt_tickets_ticket"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
    )
    expo_ticket_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    push_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)


# Partial index — the batch only ever scans not-yet-checked rows.
Index(
    "ix_push_receipt_tickets_unchecked",
    PushReceiptTicket.created_at,
    postgresql_where=(PushReceiptTicket.checked_at.is_(None)),
)
