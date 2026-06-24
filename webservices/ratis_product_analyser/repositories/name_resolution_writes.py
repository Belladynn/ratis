"""Write paths for the name-resolution consensus ledger (NRC bloc C).

Implements three responsibilities for the append-only ledger
``product_name_resolutions`` :

- ``record_resolution`` — INSERT a new validation row, idempotent on
  ``(scan_id, source_type, normalized_label)`` (UNIQUE index
  ``idx_pnr_scan_source_label`` — bloc A cross-retailer schema).
  After a real (non-conflict) insert, triggers
  ``evaluate_state_transition`` so a single call site captures both the
  ledger write and the audit event when the state actually changes.

- ``evaluate_state_transition`` — Compute the live consensus, compare
  against the last persisted state in ``pipeline_audit_log``, and emit
  a ``consensus_state_changed`` event when the state moves. Pure
  detection, no side-effects beyond the audit row.

- ``emit_consensus_state_changed_event`` — Persist the audit row. Kept
  separate so the orchestrator can emit synthetic events (replay,
  backfill bloc F) without re-running the full state diff.

All three functions operate inside the caller's transaction — they
NEVER ``commit()``. The caller owns transaction boundaries.

See ``ARCH_name_resolution_consensus.md`` § "Promotion / Détection /
États" for the full state-transition contract.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Literal

from ratis_core.models.name_resolution import ProductNameResolution
from sqlalchemy import text
from sqlalchemy.orm import Session

from repositories.consensus_state import ConsensusState
from repositories.name_resolution_repository import (
    ConsensusResult,
)
from repositories.name_resolution_repository import (
    get_consensus_for_label_by_store as get_consensus_for_label,
)

logger = logging.getLogger(__name__)


# Match methods accepted by the ``pnr_match_method_check`` constraint.
# ``'esl'`` (Bloc D — cross-retailer consensus) tags rows written by
# ``worker/label_task.py`` after a successful electronic-shelf-label
# match (pyzbar barcode or OCR EAN+checksum). The DB CHECK accepts it
# unconditionally ; the Literal here documents the V1 contract for
# callers / IDEs.
LedgerMethod = Literal["barcode", "manual_admin", "fuzzy_pending", "observed_name", "esl"]

# Source types accepted by the ``pnr_source_type_check`` constraint
# (bloc A cross-retailer schema). 'receipt' = ticket-derived rows
# (current V1 path : barcode_service, receipt matcher, manual_admin).
# 'esl' = electronic shelf label rows (bloc D — not wired yet).
LedgerSourceType = Literal["receipt", "esl"]
_VALID_SOURCE_TYPES = frozenset({"receipt", "esl"})


# ============================================================
# Anti-fraud helpers
# ============================================================


def _shadow_ban_weight_override(db: Session, *, user_id: uuid.UUID) -> int | None:
    """Return ``0`` if ``user_id`` is shadow-banned, else ``None``.

    A ``None`` return means "use the default method-derived weight" —
    which the consumer materialises in :func:`_evaluate`. The query is a
    cheap PK lookup (``users.id``) — no index miss possible.

    A missing user row (impossible in production : ``user_id`` is a NOT
    NULL FK to ``users``) returns ``None`` rather than raising — letting
    the INSERT fail later on the FK constraint produces a cleaner error
    than a defensive guard here.
    """
    row = db.execute(
        text("SELECT is_shadow_banned FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    if row is None:
        return None
    return 0 if row.is_shadow_banned else None


# ============================================================
# record_resolution
# ============================================================


def record_resolution(
    db: Session,
    *,
    scan_id: uuid.UUID,
    store_id: uuid.UUID,
    normalized_label: str,
    product_ean: str,
    user_id: uuid.UUID,
    match_method: LedgerMethod,
    source_type: str = "receipt",
) -> ProductNameResolution:
    """Insert a ledger row for ``(scan_id, source_type, normalized_label) → product_ean``.

    Idempotent on the UNIQUE index ``idx_pnr_scan_source_label`` covering
    ``(scan_id, source_type, normalized_label)``. On a UNIQUE conflict
    the existing row is returned untouched (append-only philosophy —
    never UPDATE) and ``evaluate_state_transition`` is NOT triggered
    (the state cannot have changed if no row was added).

    The caller owns the transaction : we ``flush`` to surface CHECK /
    FK violations promptly, but we never ``commit``. Pair with
    ``db.commit()`` in the route / service layer.

    Parameters
    ----------
    scan_id, store_id, normalized_label, product_ean, user_id
        See ``ProductNameResolution`` model fields.
    match_method
        One of ``barcode``, ``manual_admin``, ``fuzzy_pending``,
        ``observed_name``. Validated by the DB CHECK
        ``pnr_match_method_check`` ; a Literal here gives mypy / IDE
        completion the same guarantee.
    source_type
        Origin of the row : ``'receipt'`` (default — ticket-derived,
        the only path wired in V1 NRC bloc A) or ``'esl'`` (electronic
        shelf label, reserved for bloc D). Validated against
        ``_VALID_SOURCE_TYPES`` here AND by the DB CHECK
        ``pnr_source_type_check``. Default keeps backward-compat with
        callers written before bloc A.

    Returns
    -------
    The persisted (or pre-existing) ``ProductNameResolution`` row.

    Raises
    ------
    ValueError
        If ``source_type`` is not in ``{'receipt', 'esl'}``.
    """
    if source_type not in _VALID_SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {sorted(_VALID_SOURCE_TYPES)} ; got {source_type!r}")
    # Anti-fraud V1 (NRC) — if the user is shadow-banned, persist the
    # row with ``weight_override = 0`` so the audit trail stays append-
    # only, but the vote carries zero weight in ``_evaluate``. See
    # ``ARCH_anti_fraud.md`` § "Hook ledger".
    weight_override = _shadow_ban_weight_override(db, user_id=user_id)

    # Use raw SQL with ON CONFLICT DO NOTHING + RETURNING so we can
    # detect "real insert" vs "duplicate skipped" deterministically.
    # SQLAlchemy ORM-level INSERT does not surface that distinction
    # cleanly without a second SELECT.
    # ``clock_timestamp()`` rather than the default ``now()`` so multiple
    # ledger writes inside a single transaction stay strictly ordered
    # in time (challenger detection in (g) keys on
    # ``resolved_at > last_verified_event.created_at``).
    new_id = uuid.uuid4()
    # ``ON CONFLICT (scan_id, source_type, normalized_label)`` targets
    # the unique index ``idx_pnr_scan_source_label`` (bloc A cross-
    # retailer schema — a UNIQUE INDEX, not a NAMED CONSTRAINT — so we
    # identify it by the column tuple rather than ``ON CONSTRAINT <name>``).
    inserted = db.execute(
        text(
            """
            INSERT INTO product_name_resolutions
                (id, scan_id, store_id, normalized_label,
                 product_ean, user_id, match_method, source_type,
                 resolved_at, weight_override)
            VALUES
                (:id, :scan_id, :store_id, :normalized_label,
                 :product_ean, :user_id, :match_method, :source_type,
                 clock_timestamp(), :weight_override)
            ON CONFLICT (scan_id, source_type, normalized_label) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": new_id,
            "scan_id": str(scan_id),
            "store_id": str(store_id),
            "normalized_label": normalized_label,
            "product_ean": product_ean,
            "user_id": str(user_id),
            "match_method": match_method,
            "source_type": source_type,
            "weight_override": weight_override,
        },
    ).first()
    db.flush()

    if inserted is not None:
        # Real insert — recompute and possibly emit a state-change event.
        evaluate_state_transition(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            triggered_by_scan_id=scan_id,
        )
        return _load_resolution(db, new_id)

    # Conflict path : an earlier write already covered
    # (scan_id, source_type, label). Return the pre-existing row, do NOT
    # re-evaluate (state unchanged). Filtering on source_type matters
    # post bloc A : a single scan may legitimately hold both a receipt
    # and an ESL row for the same normalized_label.
    existing = (
        db.query(ProductNameResolution)
        .filter(
            ProductNameResolution.scan_id == scan_id,
            ProductNameResolution.source_type == source_type,
            ProductNameResolution.normalized_label == normalized_label,
        )
        .one()
    )
    return existing


def _load_resolution(db: Session, resolution_id: uuid.UUID) -> ProductNameResolution:
    """Reload the freshly-inserted row through the ORM so callers get a
    fully-attached entity (and the relationship loaders work).
    """
    row = db.get(ProductNameResolution, resolution_id)
    if row is None:  # pragma: no cover — defensive
        raise RuntimeError(
            f"product_name_resolutions row {resolution_id} not found after INSERT — transaction visibility issue?"
        )
    return row


# ============================================================
# State transition detection
# ============================================================


def evaluate_state_transition(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    triggered_by_scan_id: uuid.UUID | None = None,
) -> ConsensusState | None:
    """Recompute the consensus state and emit an audit event on change.

    Compares the live consensus state (via ``get_consensus_for_label``)
    against the most-recent persisted state in
    ``pipeline_audit_log`` (event ``consensus_state_changed`` for this
    ``(store_id, normalized_label)``). Emits a new audit row when :

    - There is no prior history AND the new state is not
      ``UNRESOLVED`` (a transition from "no consensus" to "any
      contributing state" is worth recording), OR
    - The new state differs from the last persisted state.

    Returns the NEW state (or ``None`` when the ledger is empty for
    this pair — semantically ``UNRESOLVED``, but we map ``None`` →
    no-op for the audit log to keep the noise floor at zero).
    """
    consensus = get_consensus_for_label(db, store_id=store_id, normalized_label=normalized_label)
    new_state: ConsensusState | None = consensus.state if consensus else None

    last_state = _last_persisted_state(db, store_id=store_id, normalized_label=normalized_label)

    # Decide whether to emit. Only record real transitions ; skip
    # ``None → None`` and equal-state recomputations.
    if new_state is None:
        return None

    if last_state is None or last_state != new_state:
        # ``new_state is not None`` ⇒ ``consensus`` is not None.
        assert consensus is not None  # narrow for type checkers
        emit_consensus_state_changed_event(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            from_state=last_state,
            to_state=new_state,
            consensus=consensus,
            triggered_by_scan_id=triggered_by_scan_id,
        )

    return new_state


def _last_persisted_state(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
) -> ConsensusState | None:
    """Read the last ``to_state`` for this ``(store, label)`` from the audit log.

    Returns ``None`` when no prior ``consensus_state_changed`` event
    exists. Uses ``payload->>'to_state'`` so the partial index
    ``idx_pal_consensus_state_changed`` (migration
    20260501_1700_nrcC) keeps this lookup cheap.
    """
    row = db.execute(
        text(
            """
            SELECT payload->>'to_state' AS to_state
            FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :store_id
              AND payload->>'normalized_label' = :label
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"store_id": str(store_id), "label": normalized_label},
    ).first()
    if row is None or row.to_state is None:
        return None
    try:
        return ConsensusState(row.to_state)
    except ValueError:  # pragma: no cover — defensive against schema drift
        logger.warning("unknown consensus state %r in pipeline_audit_log — ignoring", row.to_state)
        return None


# ============================================================
# Audit event emission
# ============================================================


def emit_consensus_state_changed_event(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    from_state: ConsensusState | None,
    to_state: ConsensusState,
    consensus: ConsensusResult,
    triggered_by_scan_id: uuid.UUID | None = None,
    extra_payload: dict | None = None,
) -> None:
    """INSERT a ``consensus_state_changed`` row into ``pipeline_audit_log``.

    Payload schema (per ARCH § "Promotion / Détection / États") :

        {
          "event": "consensus_state_changed",
          "store_id": "<uuid>",
          "normalized_label": "<text>",
          "from_state": "<state>|null",
          "to_state": "<state>",
          "top1_ean": "<ean>",
          "distinct_validators": <int>,
          "convergence_pct": <float>,
          "triggered_by_scan_id": "<uuid>|null",
          "challengers": [...]|null
        }

    For ``to_state == UNVERIFIED``, ``challengers`` is populated with
    the validators whose votes diverge from the previously-verified
    EAN ; for any other ``to_state`` the field is ``null``.

    The row is written at ``phase='match'`` / ``level='normal'``. Phase
    ``match`` reuses the existing audit-log channel for matcher state ;
    no new phase is introduced (matches the orchestrator's vocabulary).

    ``extra_payload`` (optional) — additional keys merged into the
    payload AFTER the standard fields are computed. Used by Bloc D
    admin actions (``reject-challenges`` injects
    ``{"action": "challenges_rejected", "rejected_user_ids": [...],
    "operator_note": "..."}``) so the audit row carries the operator-
    intent semantics on top of the regular state-transition record.
    Keys in ``extra_payload`` override the standard ones if they
    collide — caller's responsibility to avoid clashes.
    """
    payload: dict = {
        "event": "consensus_state_changed",
        "store_id": str(store_id),
        "normalized_label": normalized_label,
        "from_state": from_state.value if from_state is not None else None,
        "to_state": to_state.value,
        "top1_ean": consensus.ean,
        "distinct_validators": consensus.distinct_validators,
        "convergence_pct": float(round(consensus.top1_pct, 4)),
        "triggered_by_scan_id": (str(triggered_by_scan_id) if triggered_by_scan_id is not None else None),
        "challengers": None,
    }

    if to_state == ConsensusState.UNVERIFIED:
        payload["challengers"] = _collect_challengers(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            current_top1_ean=consensus.ean,
        )

    if extra_payload:
        payload.update(extra_payload)

    # ``clock_timestamp()`` (vs the default ``now()``) returns the
    # actual wall-clock time at statement evaluation rather than the
    # transaction start time. Multiple state-change events emitted
    # inside a single transaction (e.g. consensus walking
    # PENDING→VERIFIED→UNVERIFIED as votes pile up in batch
    # backfills) must appear in distinct chronological order so
    # ``_last_persisted_state`` and challenger detection stay
    # deterministic.
    db.execute(
        text(
            """
            INSERT INTO pipeline_audit_log
                (phase, level, event, scan_id, payload, created_at)
            VALUES
                ('match', 'normal', 'consensus_state_changed',
                 :scan_id, CAST(:payload AS jsonb), clock_timestamp())
            """
        ),
        {
            "scan_id": (str(triggered_by_scan_id) if triggered_by_scan_id is not None else None),
            "payload": json.dumps(payload),
        },
    )
    db.flush()


# ============================================================
# Anti-fraud challengers (sub-task g)
# ============================================================


def _collect_challengers(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    current_top1_ean: str,
) -> list[dict]:
    """Identify validators who voted for an EAN ≠ previously-verified-EAN.

    Reads the most recent ``to_state='verified'`` event for this
    ``(store, label)`` pair, takes the ``top1_ean`` carried in its
    payload as the previously-verified-EAN, then returns the ledger
    rows that :

    - target a different EAN, AND
    - were resolved AFTER the verified-event ``created_at``, AND
    - use a contributing method (``barcode`` or ``manual_admin``).

    Each challenger is surfaced with ``user_id``, ``scan_id``,
    ``voted_ean``, ``match_method`` and ``resolved_at`` so the admin
    queue (bloc D) can investigate.
    """
    # TODO: V1+ ratio barcode/manual + past_challenges_count per challenger
    # (fraud signal aggregates) — see ARCH § "Hors scope".
    last_verified = db.execute(
        text(
            """
            SELECT created_at, payload->>'top1_ean' AS top1_ean
            FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :store_id
              AND payload->>'normalized_label' = :label
              AND payload->>'to_state' = 'verified'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"store_id": str(store_id), "label": normalized_label},
    ).first()

    if last_verified is None or last_verified.top1_ean is None:
        # No verified history found — should not happen when called from
        # the UNVERIFIED branch (was_ever_verified=true), but defensive.
        return []

    previously_verified_ean = last_verified.top1_ean
    rows = db.execute(
        text(
            """
            SELECT user_id, scan_id, product_ean, match_method, resolved_at
            FROM product_name_resolutions
            WHERE store_id = :store_id
              AND normalized_label = :label
              AND product_ean <> :prev_ean
              AND resolved_at > :since
              AND match_method IN ('barcode', 'manual_admin')
            ORDER BY resolved_at ASC, id ASC
            """
        ),
        {
            "store_id": str(store_id),
            "label": normalized_label,
            "prev_ean": previously_verified_ean,
            "since": last_verified.created_at,
        },
    ).fetchall()

    return [
        {
            "user_id": str(r.user_id),
            "scan_id": str(r.scan_id),
            "voted_ean": r.product_ean,
            "match_method": r.match_method,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]


__all__ = [
    "LedgerMethod",
    "LedgerSourceType",
    "emit_consensus_state_changed_event",
    "evaluate_state_transition",
    "record_resolution",
]
