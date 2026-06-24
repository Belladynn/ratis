"""FraudSuspicion — admin queue for anti-fraud V1 detections.

Companion ORM for the table created in migration
``20260511_1500_afpr1`` (anti-fraud PR1 — schema foundation).

Backs the admin queue described in
``webservices/ratis_product_analyser/ARCH_receipt_pipeline.md`` §
"Réconciliation tickets — V1 (dual fingerprint + pHash + admin queue)"
(decisions acted 2026-05-11).

Schema-only in PR1 ; the application code that INSERTs / queries
rows here ships in PR2-5 (pHash pre-OCR, fingerprint compute,
cross-user policy, admin endpoints).

Notes :

- No SQLAlchemy ``ForeignKey`` to ``users`` is declared — by design.
  The audit trail must survive the RGPD anonymize flow (cf migration
  ``20260511_1000_rgpd_anon_completeness``), and the only ``users``
  link is indirect via ``receipt_id`` → ``receipts.user_id``. The
  ``admin_operator`` column is a free-form TEXT label the admin
  console fills with the operator identity ; that's RGPD-stable.
- ``evidence_receipt_ids`` is a UUID array (not a junction table) —
  the audit row must survive deletion of the cross-user receipts it
  references, so a FK contract would be wrong. The application reads
  this array at admin-review time to display the offending receipts
  (and can flag the ones that no longer exist).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    from ratis_core.models.scan import Receipt


# Allowed detection signals — must match the PG CHECK constraint
# ``ck_fraud_suspicions_signal`` declared in the migration.
DETECTION_SIGNALS = frozenset(
    {
        # pHash match cross-user inside the 30-day window
        # (Hamming distance ≤ ``pipeline.phash_hamming_threshold``).
        "phash",
        # ``fp_global`` exact match AND both receipts at
        # ``time_precision='second'`` → strict cross-user duplicate.
        "fp_global_strict",
        # ``fp_global`` exact match BUT at least one receipt at
        # ``time_precision='minute'`` (or mixed) → admin flag, not
        # an auto-reject (digit-swap OCR plausible).
        "fp_global_minute",
        # ``device_fingerprint`` observed across more than
        # ``pipeline.device_fp_distinct_users_threshold`` distinct
        # users in the configured window → trust_score penalty signal.
        "device_shared",
        # Daily soft cap fired (``receipts_soft_warn_per_day`` ≤ count <
        # ``receipts_max_per_day_per_user``) — non-blocking flag for
        # admin review of burst-like patterns. Added in anti-fraud PR4
        # (migration ``20260511_1700_afpr4`` widens the CHECK).
        "daily_soft_burst",
    }
)

# Allowed resolution statuses — must match
# ``ck_fraud_suspicions_status``.
RESOLUTION_STATUSES = frozenset({"pending", "confirmed_fraud", "cleared", "escalated_support"})


class FraudSuspicion(Base):
    """Append-on-detect, mutated-once-on-resolve audit row.

    The application code in PR2-5 INSERTs a row per detection signal
    fired during a receipt upload. The admin queue endpoint (PR5)
    lists ``resolution_status = 'pending'`` rows and lets an operator
    mark them ``confirmed_fraud`` / ``cleared`` / ``escalated_support``
    with a note. The transition is single-shot — once resolved, the
    row is immutable in V1.
    """

    __tablename__ = "fraud_suspicions"

    id: Mapped[PyUUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # The receipt that triggered this suspicion. CASCADE because the
    # row is meaningless without it ; production rarely hard-deletes
    # receipts (soft-delete via ``image_deleted_at``) so this is a
    # safety net, not a routine path.
    receipt_id: Mapped[PyUUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "receipts.id",
            ondelete="CASCADE",
            name="fraud_suspicions_receipt_id_fkey",
        ),
        nullable=False,
    )
    # The cross-user receipts that matched the signal. UUID array, not
    # a junction table — see module docstring.
    evidence_receipt_ids: Mapped[list[PyUUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False)
    detection_signal: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    resolution_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )
    admin_operator: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    receipt: Mapped["Receipt"] = relationship("Receipt", foreign_keys=[receipt_id])

    __table_args__ = (
        CheckConstraint(
            "detection_signal IN ("
            "'phash', 'fp_global_strict', 'fp_global_minute', "
            "'device_shared', 'daily_soft_burst'"
            ")",
            name="ck_fraud_suspicions_signal",
        ),
        CheckConstraint(
            "resolution_status IN ('pending', 'confirmed_fraud', 'cleared', 'escalated_support')",
            name="ck_fraud_suspicions_status",
        ),
        # Resolution coherence : ``pending`` ↔ ``resolved_at IS NULL``
        # AND ``admin_operator IS NULL``. Any other status requires
        # ``resolved_at`` to be set ; ``admin_operator`` is enforced
        # by the application (admin endpoints) but stays nullable in
        # the DB to keep the admin-action path simple.
        CheckConstraint(
            "(resolution_status = 'pending' AND resolved_at IS NULL "
            "  AND admin_operator IS NULL) "
            "OR (resolution_status <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_fraud_suspicions_resolution_coherence",
        ),
        # Partial index — admin queue list query targets pending rows.
        Index(
            "idx_fraud_suspicions_status",
            "resolution_status",
            postgresql_where=text("resolution_status = 'pending'"),
        ),
        Index("idx_fraud_suspicions_receipt", "receipt_id"),
        Index(
            "idx_fraud_suspicions_signal",
            "detection_signal",
            "resolution_status",
        ),
        Index(
            "idx_fraud_suspicions_detected_at",
            text("detected_at DESC"),
        ),
    )
