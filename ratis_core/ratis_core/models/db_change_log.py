"""``db_change_log`` — append-only journal of mutations on sensitive tables.

HSP2 — populated **exclusively** by the PL/pgSQL trigger
``fn_db_change_log_record()`` attached to the 6 sensitive tables
(``user_cab_balance``, ``cabecoin_transactions``, ``cashback_transactions``,
``cashback_withdrawals``, ``subscriptions``, ``scans``). The model exists
in the ORM for queryability (read) and so ``Base.metadata.create_all()`` in
the metadata-only tests sees the table with the same CHECK that prod has.

**Never INSERT into this table from application code.** The trigger is the
only writer. Two guard triggers (``trg_db_change_log_no_update`` and
``trg_db_change_log_no_delete``) prevent any UPDATE/DELETE — the table is
strictly append-only at the PG level too.

The CHECK constraint on ``op`` and the indexes are declared here as well
so ``Base.metadata.create_all()`` reproduces the migration shape. The
migration remains the source of truth at upgrade time.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Index,
    Text,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class DbChangeLog(Base):
    """One row per mutation observed on a sensitive table.

    Columns mirror the migration ``20260520_1000_apply_hsp2_floor`` (single
    source of truth for upgrade-time DDL).
    """

    __tablename__ = "db_change_log"
    __table_args__ = (
        CheckConstraint(
            "op IN ('insert', 'update', 'delete')",
            name="db_change_log_op_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    op: Mapped[str] = mapped_column(Text, nullable=False)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "idx_db_change_log_submission",
    DbChangeLog.submission_id,
    DbChangeLog.created_at,
    postgresql_where=sa_text("submission_id IS NOT NULL"),
)
Index(
    "idx_db_change_log_table_time",
    DbChangeLog.table_name,
    DbChangeLog.created_at,
)
