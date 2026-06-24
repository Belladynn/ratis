"""``db_write_approvals`` — table miroir des propositions d'écriture DB.

Mirror Ratis des propositions qui atteignent le gate humain du workflow
n8n ``db-write-pipeline`` (SP6). Le workflow INSERT une ligne ``pending``
via ``POST /api/v1/admin/db-approvals`` puis se met en pause sur un nœud
``Wait`` ; l'opérateur décide depuis l'UI admin ``/admin/ui/db-approvals``
et la décision (statut + opérateur + motif) est durable ici — l'audit ne
dépend pas de la rétention n8n.

Voir ``docs/superpowers/specs/2026-05-18-db-approval-ui-sp6-design.md``.

Les types sont déclarés explicitement (``TIMESTAMP(timezone=True)``,
``JSONB``) pour que ``Base.metadata.create_all()`` en test reproduise le
schéma de la migration. La migration reste la source de vérité à
l'upgrade.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Index,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class DbWriteApprovalStatus(enum.StrEnum):
    """Cycle de vie d'une proposition d'écriture au gate humain.

    ``pending`` à l'enregistrement n8n → ``approved`` / ``rejected`` sur
    décision de l'opérateur → ``expired`` si la branche timeout 24 h du
    nœud ``Wait`` se déclenche. Le mixin ``str`` rend l'enum
    interchangeable avec les valeurs PG brutes dans les filtres SQL.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class DbWriteApproval(Base):
    """Une ligne par proposition d'écriture atteignant le gate humain.

    ``submission_id`` est fourni par l'appelant (l'UUID généré par
    ``db_propose_write``) — pas de ``server_default``. ``payload`` porte
    la proposition complète (procédure, args, ``rationale``, dry-run,
    feedback LLM, ``client_message``, ``investigation``, flags).
    """

    __tablename__ = "db_write_approvals"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('execute', 'graduation')",
            name="db_write_approvals_mode_check",
        ),
    )

    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sa_text("'execute'"),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[DbWriteApprovalStatus] = mapped_column(
        SQLEnum(
            DbWriteApprovalStatus,
            name="db_write_approval_status",
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
            create_type=True,
        ),
        nullable=False,
        server_default=sa_text("'pending'"),
    )
    touches_money_tables: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
    )
    llm_unavailable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
    )
    resume_url: Mapped[str] = mapped_column(Text, nullable=False)
    operator: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# Index mirrors the migration so create_all() in test setup has it too.
Index(
    "idx_db_write_approvals_status_created",
    DbWriteApproval.status,
    DbWriteApproval.created_at.desc(),
)
