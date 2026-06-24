"""ProductNameResolution — append-only ledger for crowdsourced
``(retailer_id, source_type, normalized_label) → product_ean`` validations.

See ``webservices/ratis_product_analyser/ARCH_name_resolution_consensus.md``
(parent) and
``webservices/ratis_product_analyser/ARCH_cross_retailer_consensus.md``
(bloc A — cross-retailer + ESL elevated) for the full contract.

Schema notes (post bloc A) :

- ``source_type`` ∈ {'receipt', 'esl'} — separates ticket-derived rows
  from electronic shelf label rows. Existing rows are receipts by
  construction (NOT NULL DEFAULT 'receipt' covers them).
- ``retailer_id`` UUID NULL FK retailers ON DELETE RESTRICT — denormalized
  retailer key. Filled by the trigger ``fn_sync_pnr_retailer_id`` from
  ``stores.retailer_id`` at INSERT and on UPDATE OF ``store_id``.
  NULL-tolerant : rows from stores without a resolved retailer (e.g.
  user-suggested unvalidated stores) are kept but excluded from the
  consensus path via partial indexes ``WHERE retailer_id IS NOT NULL``.
- UNIQUE ``(scan_id, source_type, normalized_label)`` — a single scan
  can hold one receipt + one ESL row for the same label.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ratis_core.database import Base

if TYPE_CHECKING:
    # Note : User type hint removed — FK to users.id dropped here by RGPD
    # anonymize completeness (audit F-AU-3, 2026-05-11). Query directly if
    # needed : db.query(ProductNameResolution).filter_by(user_id=...).
    from ratis_core.models.retailer import Retailer
    from ratis_core.models.scan import Scan
    from ratis_core.models.store import Store


class ProductNameResolution(Base):
    __tablename__ = "product_name_resolutions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # ON DELETE CASCADE : if a scan is removed (RGPD account-delete
    # anonymisation cascade), its validations go too. The ledger keeps
    # referential integrity, never produces orphans.
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Stores are soft-deleted (R05 ``is_disabled``) — RESTRICT default is
    # the safe choice : we never want a referenced store to vanish silently.
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id"),
        nullable=False,
    )
    # Denormalized retailer reference. Filled by ``fn_sync_pnr_retailer_id``
    # trigger (BEFORE INSERT / UPDATE OF store_id). NULL-tolerant : rows
    # from stores without a resolved retailer (user-suggested unvalidated)
    # are kept but excluded from the consensus path via partial indexes.
    # ON DELETE RESTRICT : a retailer must never vanish silently while
    # a ledger row references it (R05 + audit traceability).
    retailer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retailers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    normalized_label: Mapped[str] = mapped_column(Text, nullable=False)
    product_ean: Mapped[str] = mapped_column(Text, nullable=False)
    # No ForeignKey to users — RGPD anonymize stores a per-user anon UUID here
    # (cf migration ``20260511_1000_rgpd_anon_completeness``).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    # Origin of the row : 'receipt' (ticket-derived) or 'esl' (electronic
    # shelf label). The matcher cascade and quorum rules treat the two
    # source types separately ; cross-source promotion is opt-in via the
    # match_method ``'cross_source_esl_exact'`` (V2).
    source_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'receipt'"),
    )
    # Validation method — only entries listed in
    # ``settings.name_resolution_consensus.validation_methods*`` contribute
    # to the consensus weight. ``fuzzy_pending`` and ``observed_name`` are
    # stored for traceability but excluded from the convergence calculation.
    match_method: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Anti-fraud V1 — when set, replaces the method-derived vote weight
    # in the consensus aggregation. Currently only ``0`` is written
    # (shadow-ban : audit row preserved, vote weight zero). NULL = use
    # the method-derived weight (default / non-shadow-banned path). See
    # ``ARCH_anti_fraud.md`` § "Hook ledger : weight_override".
    weight_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "match_method IN ("
            "'barcode', 'manual_admin', 'fuzzy_pending', 'observed_name', "
            "'esl', 'cross_source_esl_exact'"
            ")",
            name="pnr_match_method_check",
        ),
        CheckConstraint(
            "source_type IN ('receipt', 'esl')",
            name="pnr_source_type_check",
        ),
        # Legacy index kept for audit / per-store queries (cf.
        # ARCH_cross_retailer_consensus § Décisions schéma). Cheap.
        Index("idx_pnr_consensus", "store_id", "normalized_label"),
        # UNIQUE (scan_id, source_type, normalized_label) — a scan may
        # hold one receipt + one ESL row for the same label, but never
        # two of the same source.
        Index(
            "idx_pnr_scan_source_label",
            "scan_id",
            "source_type",
            "normalized_label",
            unique=True,
        ),
        Index("idx_pnr_user", "user_id"),
        # Hot-path consensus lookup (matcher cascade bloc B/C).
        Index(
            "idx_pnr_retailer_source_label",
            "retailer_id",
            "source_type",
            "normalized_label",
            postgresql_where=text("retailer_id IS NOT NULL"),
        ),
        # GIN trgm index for retailer-wide fuzzy consensus search.
        # Defined via raw DDL after_create (below) because the trgm op
        # class requires the pg_trgm extension and a partial WHERE clause.
    )

    scan: Mapped["Scan"] = relationship("Scan")
    store: Mapped["Store"] = relationship("Store")
    # No user relationship — FK to users.id dropped by RGPD anonymize (F-AU-3).
    # Query ProductNameResolution.user_id directly (may be an anon UUID).
    retailer: Mapped["Retailer | None"] = relationship("Retailer")


# ── Triggers attached via DDL events so ``Base.metadata.create_all()``
# (used by tests via ``conftest.py``) installs them alongside the table.
# Production runs them via the Alembic migration ``20260502_1900_xretail``
# which is the authoritative source. ``CREATE OR REPLACE`` +
# ``DROP TRIGGER IF EXISTS`` keeps both paths compatible (idempotent on
# schema rebuild).
#
# Note : the GIN trgm index ``idx_pnr_norm_label_trgm`` is NOT attached
# here — it requires the ``pg_trgm`` extension which is not installed in
# every service's test conftest. Convention ratis : indexes that depend
# on extensions (GIN trgm, partial WHERE on extension types) live in the
# migration only. Tests that need fuzzy retailer-wide lookup must create
# the index manually in their service-local conftest after pg_trgm is
# loaded (cf. ``conftest.py`` PA — pattern existant pour ``gin_products_name``).
# ---------------------------------------------------------------------------

_SYNC_RETAILER_ID_FN = DDL(
    """
    CREATE OR REPLACE FUNCTION fn_sync_pnr_retailer_id()
    RETURNS TRIGGER AS $$
    BEGIN
        -- Only denorm when application code did NOT set retailer_id
        -- explicitly. Defensive : the matcher / repos write store_id
        -- only ; this trigger fills retailer_id from stores.
        IF NEW.store_id IS NOT NULL AND NEW.retailer_id IS NULL THEN
            NEW.retailer_id := (
                SELECT retailer_id FROM stores WHERE id = NEW.store_id
            );
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
)

_SYNC_RETAILER_ID_TRIGGER = DDL(
    """
    DROP TRIGGER IF EXISTS trg_pnr_sync_retailer_id ON product_name_resolutions;
    CREATE TRIGGER trg_pnr_sync_retailer_id
    BEFORE INSERT OR UPDATE OF store_id ON product_name_resolutions
    FOR EACH ROW EXECUTE FUNCTION fn_sync_pnr_retailer_id();
    """
)

# Attach to ProductNameResolution.__table__ — fires after the table is
# created (and after FK-dependency tables retailers / stores already
# exist, since SA's create_all emits DDL in dependency order).
event.listen(
    ProductNameResolution.__table__,
    "after_create",
    _SYNC_RETAILER_ID_FN.execute_if(dialect="postgresql"),
)
event.listen(
    ProductNameResolution.__table__,
    "after_create",
    _SYNC_RETAILER_ID_TRIGGER.execute_if(dialect="postgresql"),
)
