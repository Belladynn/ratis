"""Admin service layer for the Name Resolution Consensus arbitration queue
(NRC bloc D).

Surfaces five operations consumed by both the JSON API
(``routes/admin/name_resolutions.py``) and the mini admin UI
(``admin_ui/routes.py`` Page F) :

- :func:`list_arbitration_queue` — paginated labels needing admin
  attention (``unverified`` first, then ``controverse``). Aggregates
  top-EAN distribution, validator count, sample scans.
- :func:`list_unmatched_queue` — paginated scans without a consensus
  ledger row, grouped by ``(store_id, normalized_label)``. (Post matcher
  consensus-only refonte 2026-05-02 : ``scans.candidate_eans`` is gone ;
  every non-resolved scan surfaces here.)
- :func:`get_label_detail` — full detail for one ``(store, label)`` :
  current state, all ledger rows, all ``consensus_state_changed`` audit
  events. Used by the detail page.
- :func:`resolve_label` — admin tranchant : appends a ``manual_admin``
  ledger row weight 5 to push the consensus toward ``target_ean``.
- :func:`reject_challenges` — admin re-promoting a fallen ``unverified``
  consensus by appending ``manual_admin`` weight 5 on the previously
  verified EAN. Emits a special audit payload tagging
  ``action=challenges_rejected``.
- :func:`escalate_label` — flag-only audit event for triage priorisation.

All write operations route through :func:`record_resolution` (Bloc C)
which itself triggers the state-recompute event via
``evaluate_state_transition``. The reject-challenges flow needs a
custom payload (operator note + rejected user ids) so it bypasses
``record_resolution``'s built-in event by detecting and re-emitting
through ``emit_consensus_state_changed_event(extra_payload=...)`` —
documented inline below.

Service-layer functions never call ``db.commit()`` (per layered arch
R03/R12). Callers (route handlers) own the transaction boundary.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from ratis_core.models.user import User
from repositories.consensus_state import ConsensusState
from repositories.name_resolution_repository import (
    _consensus_settings,
)
from repositories.name_resolution_repository import (
    get_consensus_for_label_by_store as get_consensus_for_label,
)
from repositories.name_resolution_writes import (
    emit_consensus_state_changed_event,
    record_resolution,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ============================================================
# Constants — admin user lookup
# ============================================================

# Stable handle for the technical admin row seeded by migration
# 20260501_2000_nrcD. Used as ``user_id`` on every ``manual_admin``
# ledger row produced by the Bloc D admin endpoints. Looking up by
# ``support_id`` (rather than the hardcoded UUID) keeps the indirection
# loose : if a future migration ever moves the row, the lookup still
# resolves.
ADMIN_SUPPORT_ID = "RTS-ADMIN0"
_ADMIN_EMAIL = "admin@ratis.internal"
_ADMIN_DISPLAY_NAME = "ratis admin (system)"


def _get_or_create_admin_user(db: Session) -> uuid.UUID:
    """Return the technical admin user's id, creating the row on demand.

    Production path : the migration ``20260501_2000_nrcD`` seeds the row
    so this function returns immediately on the first ``SELECT``.

    Test path : conftest uses ``Base.metadata.create_all`` rather than
    Alembic, so the seed is not present. We lazily INSERT the row on the
    first admin action — idempotent on the UNIQUE ``support_id`` index.

    The function never ``commit``s : the caller's transaction owns the
    row creation alongside the ledger write.
    """
    row = db.execute(
        text("SELECT id FROM users WHERE support_id = :sid"),
        {"sid": ADMIN_SUPPORT_ID},
    ).first()
    if row is not None:
        return uuid.UUID(str(row.id))

    # Lazy create — needed only for tests where the migration didn't run.
    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO users
                (id, email, support_id, account_type, display_name, is_deleted)
            VALUES
                (:id, :email, :sid, 'internal', :display_name, false)
            ON CONFLICT (support_id) DO NOTHING
            """
        ),
        {
            "id": str(new_id),
            "email": _ADMIN_EMAIL,
            "sid": ADMIN_SUPPORT_ID,
            "display_name": _ADMIN_DISPLAY_NAME,
        },
    )
    db.flush()

    # Re-select : the ON CONFLICT path returned no row, so the previously
    # existing row's id is the right answer (race with another concurrent
    # insert).
    row = db.execute(
        text("SELECT id FROM users WHERE support_id = :sid"),
        {"sid": ADMIN_SUPPORT_ID},
    ).first()
    if row is None:  # pragma: no cover — defensive
        raise RuntimeError("admin user seed failed — neither found nor inserted")
    return uuid.UUID(str(row.id))


# ============================================================
# Errors
# ============================================================


class LabelNotFound(Exception):
    """Raised when a ``(store_id, normalized_label)`` pair has no ledger
    rows AND no scans referencing it — there is nothing to detail or
    arbitrate.
    """


class StateMismatch(Exception):
    """Raised when an action requires a specific state (e.g. reject-
    challenges only valid on ``unverified``) and the current state
    differs.
    """


# ============================================================
# Data shapes
# ============================================================


@dataclass(frozen=True)
class TopEan:
    ean: str
    weighted_count: int
    pct: float
    product_name: str | None


@dataclass(frozen=True)
class SampleScan:
    scan_id: str
    scanned_name: str | None
    user_id: str | None


@dataclass(frozen=True)
class QueueItem:
    store_id: str
    store_name: str | None
    normalized_label: str
    current_state: str
    distinct_validators: int
    top_eans: list[TopEan]
    previously_verified_ean: str | None
    first_resolution_at: str | None
    last_resolution_at: str | None
    challenger_count: int
    sample_scans: list[SampleScan]


@dataclass(frozen=True)
class UnmatchedItem:
    store_id: str
    store_name: str | None
    normalized_label: str
    scan_count: int
    sample_scans: list[SampleScan]
    top_candidates: list[dict[str, Any]]  # {"ean": str, "score": float, "occurrences": int}


# ============================================================
# Queue listing (state ∈ {unverified, controverse})
# ============================================================


_QUEUE_STATES_FILTER = {
    "unverified": (ConsensusState.UNVERIFIED,),
    "controverse": (ConsensusState.CONTROVERSE,),
    "all": (ConsensusState.UNVERIFIED, ConsensusState.CONTROVERSE),
}


def list_arbitration_queue(
    db: Session,
    *,
    state_filter: str = "all",
    store_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[QueueItem], int]:
    """Return arbitration queue items + total count.

    Sort order : ``unverified`` first (priority alert), then
    ``controverse``, then by ``last_resolution_at DESC`` within each
    state. The ordering is enforced in Python after re-evaluating the
    consensus for each candidate pair (there is no SQL projection of
    ``ConsensusState`` — the enum is computed at read time per the Bloc
    A repository).
    """
    if state_filter not in _QUEUE_STATES_FILTER:
        raise ValueError(f"invalid state_filter: {state_filter!r}")
    target_states = set(_QUEUE_STATES_FILTER[state_filter])

    settings = _consensus_settings()
    methods = list(settings["validation_methods"])
    min_users = int(settings["min_distinct_users"])
    if not methods:
        return [], 0

    # Pre-filter SQL : pairs with at least quorum's worth of distinct
    # users on contributing methods. Optionally narrow by store. We do
    # NOT push the convergence check into SQL — see Bloc A repo's
    # ``list_divergent_labels`` rationale (Python eval is cheaper at V1
    # scale and reuses the canonical ``_evaluate``).
    base_sql = """
        SELECT store_id, normalized_label,
               MIN(resolved_at) AS first_at,
               MAX(resolved_at) AS last_at
        FROM product_name_resolutions
        WHERE match_method = ANY(CAST(:methods AS text[]))
        {store_clause}
        GROUP BY store_id, normalized_label
        HAVING COUNT(DISTINCT user_id) >= :min_users
        ORDER BY MAX(resolved_at) DESC
    """
    store_clause = "AND store_id = CAST(:sid AS uuid)" if store_id is not None else ""
    sql = base_sql.format(store_clause=store_clause)
    params: dict[str, Any] = {"methods": methods, "min_users": min_users}
    if store_id is not None:
        params["sid"] = str(store_id)

    candidate_pairs = db.execute(text(sql), params).fetchall()

    items: list[QueueItem] = []
    for row in candidate_pairs:
        result = get_consensus_for_label(db, store_id=row.store_id, normalized_label=row.normalized_label)
        if result is None or result.state not in target_states:
            continue
        item = _hydrate_queue_item(
            db,
            store_id=row.store_id,
            normalized_label=row.normalized_label,
            current_state=result.state,
            first_at=row.first_at,
            last_at=row.last_at,
        )
        items.append(item)

    # Stable sort : UNVERIFIED first, then CONTROVERSE, each ordered by
    # last_at desc. Python's sort is stable so the SQL ORDER BY is
    # preserved for ties.
    state_priority = {ConsensusState.UNVERIFIED.value: 0, ConsensusState.CONTROVERSE.value: 1}
    items.sort(key=lambda it: state_priority.get(it.current_state, 99))

    total = len(items)
    paged = items[offset : offset + limit]
    return paged, total


def _hydrate_queue_item(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    current_state: ConsensusState,
    first_at: Any,
    last_at: Any,
) -> QueueItem:
    """Compose a :class:`QueueItem` for one ``(store, label)`` pair.

    Aggregates :
    - store name
    - top_eans (weighted sum + pct)
    - previously_verified_ean (only when state=unverified)
    - challenger_count (only when state=unverified)
    - sample_scans (3 contributing rows for context)
    """
    settings = _consensus_settings()
    methods = list(settings["validation_methods"])
    admin_weight = int(settings["admin_validation_weight"])

    # Store name
    store_row = db.execute(
        text("SELECT name FROM stores WHERE id = :sid"),
        {"sid": str(store_id)},
    ).first()
    store_name = store_row.name if store_row is not None else None

    # Per-EAN weighted distribution. Use a CASE in SQL so the weight is
    # computed inline (matches the canonical formula in
    # ``get_consensus_for_label``).
    ean_rows = db.execute(
        text(
            """
            SELECT
                product_ean,
                SUM(
                    CASE WHEN match_method = 'manual_admin' THEN :admin_weight
                         ELSE 1 END
                ) AS weighted_count
            FROM product_name_resolutions
            WHERE store_id = :sid
              AND normalized_label = :label
              AND match_method = ANY(CAST(:methods AS text[]))
            GROUP BY product_ean
            ORDER BY weighted_count DESC
            """
        ),
        {
            "sid": str(store_id),
            "label": normalized_label,
            "methods": methods,
            "admin_weight": admin_weight,
        },
    ).fetchall()
    total_weight = sum(int(r.weighted_count) for r in ean_rows) or 1
    eans_for_lookup = [r.product_ean for r in ean_rows]
    name_by_ean = _names_for_eans(db, eans_for_lookup)
    top_eans = [
        TopEan(
            ean=str(r.product_ean),
            weighted_count=int(r.weighted_count),
            pct=round(int(r.weighted_count) / total_weight * 100.0, 2),
            product_name=name_by_ean.get(str(r.product_ean)),
        )
        for r in ean_rows
    ]

    # distinct validators
    distinct_validators_row = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT user_id) AS n
            FROM product_name_resolutions
            WHERE store_id = :sid
              AND normalized_label = :label
              AND match_method = ANY(CAST(:methods AS text[]))
            """
        ),
        {"sid": str(store_id), "label": normalized_label, "methods": methods},
    ).first()
    distinct_validators = int(distinct_validators_row.n) if distinct_validators_row else 0

    # previously verified EAN + challenger count — only meaningful when
    # state=unverified.
    previously_verified_ean: str | None = None
    challenger_count = 0
    if current_state == ConsensusState.UNVERIFIED:
        previously_verified_ean, challenger_count = _previously_verified_and_challengers(
            db, store_id=store_id, normalized_label=normalized_label
        )

    # sample scans : 3 most recent contributing scans for visual context
    sample_rows = db.execute(
        text(
            """
            SELECT pnr.scan_id, s.scanned_name, s.user_id
            FROM product_name_resolutions pnr
            LEFT JOIN scans s ON s.id = pnr.scan_id
            WHERE pnr.store_id = :sid
              AND pnr.normalized_label = :label
              AND pnr.match_method = ANY(CAST(:methods AS text[]))
            ORDER BY pnr.resolved_at DESC, pnr.id DESC
            LIMIT 3
            """
        ),
        {"sid": str(store_id), "label": normalized_label, "methods": methods},
    ).fetchall()
    sample_scans = [
        SampleScan(
            scan_id=str(r.scan_id),
            scanned_name=r.scanned_name,
            user_id=str(r.user_id) if r.user_id else None,
        )
        for r in sample_rows
    ]

    return QueueItem(
        store_id=str(store_id),
        store_name=store_name,
        normalized_label=normalized_label,
        current_state=current_state.value,
        distinct_validators=distinct_validators,
        top_eans=top_eans,
        previously_verified_ean=previously_verified_ean,
        first_resolution_at=first_at.isoformat() if first_at else None,
        last_resolution_at=last_at.isoformat() if last_at else None,
        challenger_count=challenger_count,
        sample_scans=sample_scans,
    )


def _names_for_eans(db: Session, eans: list[str]) -> dict[str, str | None]:
    """Best-effort lookup of ``products.name`` for a list of EANs.

    EANs absent from ``products`` map to ``None`` — the admin queue
    surfaces the EAN string regardless so the operator still has the
    raw identifier.
    """
    if not eans:
        return {}
    rows = db.execute(
        text("SELECT ean, name FROM products WHERE ean = ANY(CAST(:eans AS text[]))"),
        {"eans": eans},
    ).fetchall()
    return {str(r.ean): r.name for r in rows}


def _previously_verified_and_challengers(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
) -> tuple[str | None, int]:
    """Read the last verified-event in audit log + count challenger ids.

    Returns ``(previously_verified_ean, challenger_count)``. When no
    verified event exists, returns ``(None, 0)`` (defensive — should
    not happen for ``UNVERIFIED`` rows by construction).
    """
    last_verified = db.execute(
        text(
            """
            SELECT created_at, payload->>'top1_ean' AS top1_ean
            FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :sid
              AND payload->>'normalized_label' = :label
              AND payload->>'to_state' = 'verified'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).first()
    if last_verified is None or last_verified.top1_ean is None:
        return None, 0
    prev_ean = str(last_verified.top1_ean)
    count_row = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT user_id) AS n
            FROM product_name_resolutions
            WHERE store_id = :sid
              AND normalized_label = :label
              AND product_ean <> :prev_ean
              AND resolved_at > :since
              AND match_method IN ('barcode', 'manual_admin')
            """
        ),
        {
            "sid": str(store_id),
            "label": normalized_label,
            "prev_ean": prev_ean,
            "since": last_verified.created_at,
        },
    ).first()
    return prev_ean, int(count_row.n) if count_row else 0


# ============================================================
# Unmatched queue (fuzzy candidates without consensus)
# ============================================================


def list_unmatched_queue(
    db: Session,
    *,
    store_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[UnmatchedItem], int]:
    """Return scans with no consensus row in the ledger.

    Grouped by ``(store_id, normalized_label)``. Each group surfaces :
    - scan_count (how many distinct scans share that label)
    - sample_scans (3 most recent for context)
    - top_candidates (always empty after the matcher consensus-only
      refonte 2026-05-02 — there are no fuzzy fallback candidates left
      to aggregate ; field kept for response-shape back-compat)

    Following the refonte, every scan that did not reach a verified
    consensus surfaces here ; admins resolve via :func:`resolve_label`
    which records a ``manual_admin`` ledger row.
    """
    base_sql = """
        SELECT store_id, scanned_name AS normalized_label,
               COUNT(*) AS scan_count
        FROM scans
        WHERE store_id IS NOT NULL
          AND scanned_name IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM product_name_resolutions pnr
              WHERE pnr.store_id = scans.store_id
                AND pnr.normalized_label = scans.scanned_name
          )
        {store_clause}
        GROUP BY store_id, scanned_name
        ORDER BY scan_count DESC, store_id, scanned_name
    """
    store_clause = "AND store_id = CAST(:sid AS uuid)" if store_id is not None else ""
    sql = base_sql.format(store_clause=store_clause)
    params: dict[str, Any] = {}
    if store_id is not None:
        params["sid"] = str(store_id)

    rows = db.execute(text(sql), params).fetchall()
    total = len(rows)
    paged = rows[offset : offset + limit]

    items = [
        _hydrate_unmatched_item(
            db,
            store_id=r.store_id,
            normalized_label=r.normalized_label,
            scan_count=int(r.scan_count),
        )
        for r in paged
    ]
    return items, total


def _hydrate_unmatched_item(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    scan_count: int,
) -> UnmatchedItem:
    """Compose the aggregated view for one unmatched ``(store, label)``."""
    store_row = db.execute(
        text("SELECT name FROM stores WHERE id = :sid"),
        {"sid": str(store_id)},
    ).first()
    store_name = store_row.name if store_row is not None else None

    sample_rows = db.execute(
        text(
            """
            SELECT id AS scan_id, scanned_name, user_id
            FROM scans
            WHERE store_id = :sid
              AND scanned_name = :label
            ORDER BY scanned_at DESC, id
            LIMIT 3
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).fetchall()

    sample_scans = [
        SampleScan(
            scan_id=str(r.scan_id),
            scanned_name=r.scanned_name,
            user_id=str(r.user_id) if r.user_id else None,
        )
        for r in sample_rows
    ]

    # ``top_candidates`` is preserved as an empty list to keep the
    # response shape stable for the admin UI (no breaking change in JSON
    # contract). After the matcher consensus-only refonte the matcher
    # never produces fuzzy candidates, so there is nothing to aggregate.
    top_candidates: list[dict[str, Any]] = []

    return UnmatchedItem(
        store_id=str(store_id),
        store_name=store_name,
        normalized_label=normalized_label,
        scan_count=scan_count,
        sample_scans=sample_scans,
        top_candidates=top_candidates,
    )


# ============================================================
# Detail (single label)
# ============================================================


def get_label_detail(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
) -> dict[str, Any]:
    """Aggregate every ledger row + state-change event for a label.

    Returns a dict ready to JSON-serialize. Raises :class:`LabelNotFound`
    when no ledger row AND no audit event exist for this pair (cleaner
    than returning ``{}`` and letting the route 404 by accident).
    """
    store_row = db.execute(
        text("SELECT id, name FROM stores WHERE id = :sid"),
        {"sid": str(store_id)},
    ).first()

    ledger_rows = db.execute(
        text(
            """
            SELECT id, scan_id, product_ean, user_id, match_method, resolved_at
            FROM product_name_resolutions
            WHERE store_id = :sid AND normalized_label = :label
            ORDER BY resolved_at, id
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).fetchall()

    audit_rows = db.execute(
        text(
            """
            SELECT id, event, payload, created_at
            FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :sid
              AND payload->>'normalized_label' = :label
            ORDER BY created_at, id
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).fetchall()

    if not ledger_rows and not audit_rows:
        raise LabelNotFound(f"no resolutions or audit events for ({store_id}, {normalized_label!r})")

    consensus = get_consensus_for_label(db, store_id=store_id, normalized_label=normalized_label)
    current_state = consensus.state.value if consensus else ConsensusState.UNRESOLVED.value

    # Identify challenger user_ids (votes after the last verified event
    # on a different EAN). Used by the UI to highlight rows.
    prev_ean, _challenger_count = _previously_verified_and_challengers(
        db, store_id=store_id, normalized_label=normalized_label
    )
    challenger_user_ids: set[str] = set()
    if prev_ean is not None:
        last_verified = db.execute(
            text(
                """
                SELECT created_at FROM pipeline_audit_log
                WHERE event = 'consensus_state_changed'
                  AND payload->>'store_id' = :sid
                  AND payload->>'normalized_label' = :label
                  AND payload->>'to_state' = 'verified'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"sid": str(store_id), "label": normalized_label},
        ).first()
        if last_verified is not None:
            cr = db.execute(
                text(
                    """
                    SELECT DISTINCT user_id::text AS uid
                    FROM product_name_resolutions
                    WHERE store_id = :sid
                      AND normalized_label = :label
                      AND product_ean <> :prev_ean
                      AND resolved_at > :since
                      AND match_method IN ('barcode', 'manual_admin')
                    """
                ),
                {
                    "sid": str(store_id),
                    "label": normalized_label,
                    "prev_ean": prev_ean,
                    "since": last_verified.created_at,
                },
            ).fetchall()
            challenger_user_ids = {str(row.uid) for row in cr}

    return {
        "store_id": str(store_id),
        "store_name": store_row.name if store_row is not None else None,
        "normalized_label": normalized_label,
        "current_state": current_state,
        "previously_verified_ean": prev_ean,
        "resolutions": [
            {
                "id": str(r.id),
                "scan_id": str(r.scan_id),
                "product_ean": r.product_ean,
                "user_id": str(r.user_id),
                "match_method": r.match_method,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "is_challenger": str(r.user_id) in challenger_user_ids,
            }
            for r in ledger_rows
        ],
        "events": [
            {
                "id": str(a.id),
                "event": a.event,
                "payload": a.payload,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in audit_rows
        ],
    }


# ============================================================
# Resolve (write : append manual_admin row)
# ============================================================


def resolve_label(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    target_ean: str,
    operator: str,
    operator_note: str | None = None,
) -> dict[str, Any]:
    """Append a ``manual_admin`` ledger row pushing consensus toward ``target_ean``.

    Reuses :func:`record_resolution` (Bloc C) so :
    - the UNIQUE ``(scan_id, normalized_label)`` index prevents duplicate
      writes when the same ``(target_ean, operator_note)`` tuple is
      replayed,
    - the state-recompute event is emitted automatically.

    The ledger row needs a ``scan_id`` (FK NOT NULL) — we attach the row
    to a recent scan that carries the label so the audit trail still
    points at a real-world artefact. If no scan exists, fall back to
    creating a *synthetic* admin scan row (rare path, defensive).
    Without an attachable scan we surface an explicit error rather than
    silently failing the FK.
    """
    # Look up an existing scan to anchor the ledger row BEFORE creating
    # the admin user — otherwise a 404 path would leave an uncommitted
    # users row in the test transaction (and a real prod transaction
    # too, even though Postgres rolls it back on the eventual error
    # path).
    #
    # The ``record_resolution`` writer uses
    # ``ON CONFLICT (scan_id, normalized_label) DO NOTHING`` so we MUST
    # pick a scan that does NOT yet hold a ledger row for this exact
    # label — otherwise the admin row would be silently skipped and the
    # consensus would not move. Prefer the most recent free scan ; fall
    # back to creating a synthetic admin-anchor scan when every existing
    # scan is already attached.
    scan_row = db.execute(
        text(
            """
            SELECT s.id
            FROM scans s
            WHERE s.store_id = :sid
              AND s.scanned_name = :label
              AND NOT EXISTS (
                  SELECT 1 FROM product_name_resolutions p
                  WHERE p.scan_id = s.id
                    AND p.normalized_label = s.scanned_name
              )
            ORDER BY s.scanned_at DESC, s.id
            LIMIT 1
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).first()
    if scan_row is None:
        # Either no scan exists at all OR every scan is already taken.
        # Distinguish the two : if at least one scan references this
        # (store, label), we can synthesise an admin-anchor scan so the
        # resolve still applies.
        any_scan = db.execute(
            text("SELECT 1 FROM scans WHERE store_id = :sid AND scanned_name = :label LIMIT 1"),
            {"sid": str(store_id), "label": normalized_label},
        ).first()
        if any_scan is None:
            raise LabelNotFound(
                f"no scan found for ({store_id}, {normalized_label!r}) — admin "
                "resolve requires at least one scan to anchor the ledger row"
            )

    admin_user_id = _get_or_create_admin_user(db)

    if scan_row is None:
        scan_row_id = _create_admin_anchor_scan(
            db,
            store_id=store_id,
            target_ean=target_ean,
            admin_user_id=admin_user_id,
        )
    else:
        scan_row_id = uuid.UUID(str(scan_row.id))

    # Capture state before write to surface ``from_state`` in the audit
    # trail and the response. ``record_resolution`` triggers its own
    # state-change event when applicable, so we don't re-emit here.
    state_before = get_consensus_for_label(db, store_id=store_id, normalized_label=normalized_label)

    record_resolution(
        db,
        scan_id=scan_row_id,
        store_id=store_id,
        normalized_label=normalized_label,
        product_ean=target_ean,
        user_id=admin_user_id,
        match_method="manual_admin",
        source_type="receipt",
    )

    # Always emit a separate ``manual`` audit event tagging the operator
    # action (with note + operator handle) — distinct from the
    # automatic state-change event. This makes the operator intent
    # discoverable even when the consensus state did NOT change (e.g.
    # admin re-affirms an already-verified label).
    _emit_admin_action_event(
        db,
        store_id=store_id,
        normalized_label=normalized_label,
        action="resolve",
        operator=operator,
        operator_note=operator_note,
        extra={"target_ean": target_ean, "anchored_scan_id": str(scan_row_id)},
    )

    state_after = get_consensus_for_label(db, store_id=store_id, normalized_label=normalized_label)

    return {
        "store_id": str(store_id),
        "normalized_label": normalized_label,
        "target_ean": target_ean,
        "anchored_scan_id": str(scan_row_id),
        "from_state": state_before.state.value if state_before else None,
        "to_state": state_after.state.value if state_after else None,
    }


def _create_admin_anchor_scan(
    db: Session,
    *,
    store_id: uuid.UUID,
    target_ean: str,
    admin_user_id: uuid.UUID,
) -> uuid.UUID:
    """Insert a synthetic ``scan_type='manual'`` row owned by the admin user.

    Used as a fallback when every existing scan attached to ``(store,
    label)`` already has a ledger row : the UNIQUE
    ``(scan_id, normalized_label)`` index would silently skip the admin
    insert otherwise, leaving the consensus untouched. The synthetic
    scan carries minimal fields ; its presence in the audit trail is
    documented via the ``admin_name_resolution_resolve`` event payload.

    The PG CHECK ``manual_no_scanned_name`` requires every ``scan_type
    ='manual'`` row to carry ``product_ean IS NOT NULL`` and
    ``scanned_name IS NULL`` — so we anchor the synthetic row on
    ``target_ean`` (the EAN the admin is resolving toward) with a NULL
    ``scanned_name``. The label is preserved on the sibling
    ``product_name_resolutions`` row via its own ``normalized_label``
    column, which is what the consensus query reads.

    Discovery (2026-05-11) : earlier shape stored ``scanned_name
    =:label`` + ``product_ean=NULL`` which violates the CHECK in PG.
    The ORM mirror was missing so tests via ``Base.metadata.create_all``
    didn't surface it ; the only callable path was the rare admin
    fallback (no existing scan rows for the label) which had no
    coverage. Caught by the Pattern A roll-out audit (Bug 6).
    """
    # ``status='pending'`` keeps the row out of the v3 ``matched``
    # invariant (which requires ``product_ean`` + ``match_method``). The
    # synthetic row is purely a ledger anchor — it has no semantic
    # meaning beyond satisfying the FK on
    # ``product_name_resolutions.scan_id``.
    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO scans
                (id, scan_type, status, scanned_name, product_ean, store_id,
                 user_id, price, quantity, scanned_at)
            VALUES
                (:id, 'manual', 'pending', NULL, :ean, :sid,
                 :uid, 0, 1, clock_timestamp())
            """
        ),
        {
            "id": str(new_id),
            "ean": target_ean,
            "sid": str(store_id),
            "uid": str(admin_user_id),
        },
    )
    db.flush()
    return new_id


# ============================================================
# Reject challenges (re-promote previously-verified EAN)
# ============================================================


def reject_challenges(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    operator: str,
    operator_note: str | None = None,
) -> dict[str, Any]:
    """Re-promote the previously-verified EAN by appending ``manual_admin``.

    Only valid when the current state is ``UNVERIFIED`` — raises
    :class:`StateMismatch` otherwise. Reads the previously-verified EAN
    from the audit log, then takes the same code path as
    :func:`resolve_label` (one ledger row weight 5) on that EAN.

    On top of the standard state-change event emitted by
    ``record_resolution``, we also emit a dedicated audit row carrying
    ``action="challenges_rejected"`` + the rejected challenger user_ids
    + the operator note, so the audit trail captures the operator-
    intent (the standard event would only show the state-recovery, not
    the *why*).
    """
    # State gate — fail-fast before touching the ledger.
    consensus = get_consensus_for_label(db, store_id=store_id, normalized_label=normalized_label)
    if consensus is None or consensus.state != ConsensusState.UNVERIFIED:
        actual = consensus.state.value if consensus else ConsensusState.UNRESOLVED.value
        raise StateMismatch(f"reject_challenges requires state=unverified, got {actual!r}")

    prev_ean, _challenger_count = _previously_verified_and_challengers(
        db, store_id=store_id, normalized_label=normalized_label
    )
    if prev_ean is None:
        # Defensive — should not happen when state=UNVERIFIED by construction.
        raise StateMismatch("no previously-verified EAN found in audit log — cannot reject challenges")

    # Collect the challenger user ids for the audit payload.
    rejected_user_ids = _list_challenger_user_ids(
        db, store_id=store_id, normalized_label=normalized_label, prev_ean=prev_ean
    )

    admin_user_id = _get_or_create_admin_user(db)

    # Pick a scan that doesn't already hold a ledger row for this label
    # (same rationale as :func:`resolve_label` — the UNIQUE index would
    # silently skip the admin write otherwise). Fall back to a synthetic
    # admin-anchor scan when every existing scan is taken.
    scan_row = db.execute(
        text(
            """
            SELECT s.id
            FROM scans s
            WHERE s.store_id = :sid
              AND s.scanned_name = :label
              AND NOT EXISTS (
                  SELECT 1 FROM product_name_resolutions p
                  WHERE p.scan_id = s.id
                    AND p.normalized_label = s.scanned_name
              )
            ORDER BY s.scanned_at DESC, s.id
            LIMIT 1
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).first()
    if scan_row is None:
        anchor_scan_id = _create_admin_anchor_scan(
            db,
            store_id=store_id,
            target_ean=prev_ean,
            admin_user_id=admin_user_id,
        )
    else:
        anchor_scan_id = uuid.UUID(str(scan_row.id))

    state_before = consensus  # already loaded above
    record_resolution(
        db,
        scan_id=anchor_scan_id,
        store_id=store_id,
        normalized_label=normalized_label,
        product_ean=prev_ean,
        user_id=admin_user_id,
        match_method="manual_admin",
        source_type="receipt",
    )

    # Re-emit a state-change event carrying the action-specific payload.
    # ``record_resolution`` already emitted the canonical state-change
    # event ; this second event documents the operator INTENT (action=
    # challenges_rejected). The ARCH spec requires this distinction so
    # the audit log captures the *why* on top of the *what*.
    state_after = get_consensus_for_label(db, store_id=store_id, normalized_label=normalized_label)
    if state_after is not None:
        emit_consensus_state_changed_event(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            from_state=state_before.state,
            to_state=state_after.state,
            consensus=state_after,
            triggered_by_scan_id=anchor_scan_id,
            extra_payload={
                "action": "challenges_rejected",
                "rejected_user_ids": rejected_user_ids,
                "operator": operator,
                "operator_note": operator_note,
            },
        )

    return {
        "store_id": str(store_id),
        "normalized_label": normalized_label,
        "previously_verified_ean": prev_ean,
        "rejected_user_ids": rejected_user_ids,
        "from_state": state_before.state.value,
        "to_state": state_after.state.value if state_after else None,
    }


def _list_challenger_user_ids(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    prev_ean: str,
) -> list[str]:
    """Return the distinct user_ids whose votes diverge from prev_ean."""
    last_verified = db.execute(
        text(
            """
            SELECT created_at FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :sid
              AND payload->>'normalized_label' = :label
              AND payload->>'to_state' = 'verified'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).first()
    if last_verified is None:
        return []
    rows = db.execute(
        text(
            """
            SELECT DISTINCT user_id::text AS uid
            FROM product_name_resolutions
            WHERE store_id = :sid
              AND normalized_label = :label
              AND product_ean <> :prev_ean
              AND resolved_at > :since
              AND match_method IN ('barcode', 'manual_admin')
            ORDER BY uid
            """
        ),
        {
            "sid": str(store_id),
            "label": normalized_label,
            "prev_ean": prev_ean,
            "since": last_verified.created_at,
        },
    ).fetchall()
    return [str(r.uid) for r in rows]


# ============================================================
# Escalate (flag-only audit event)
# ============================================================


def escalate_label(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    operator: str,
    operator_note: str | None = None,
) -> dict[str, Any]:
    """Tag a label for priorisation manual review.

    No business side-effect — emits a single ``admin_escalated`` event
    in ``pipeline_audit_log`` (phase=manual) so the operator's flag
    surfaces in queries / dashboards. Idempotent in spirit (multiple
    escalations stack as separate audit rows for traceability).
    """
    # Verify the label exists somewhere — refuse to escalate a phantom
    # ``(store, label)``.
    exists = db.execute(
        text(
            """
            SELECT 1 FROM product_name_resolutions
            WHERE store_id = :sid AND normalized_label = :label
            LIMIT 1
            """
        ),
        {"sid": str(store_id), "label": normalized_label},
    ).first()
    if exists is None:
        # Fallback : tolerate an unmatched label (no ledger row but at
        # least one scan referencing it).
        exists = db.execute(
            text(
                """
                SELECT 1 FROM scans
                WHERE store_id = :sid AND scanned_name = :label
                LIMIT 1
                """
            ),
            {"sid": str(store_id), "label": normalized_label},
        ).first()
    if exists is None:
        raise LabelNotFound(f"({store_id}, {normalized_label!r}) — no ledger row and no scan")

    _emit_admin_action_event(
        db,
        store_id=store_id,
        normalized_label=normalized_label,
        action="escalate",
        operator=operator,
        operator_note=operator_note,
        extra={},
    )
    return {
        "store_id": str(store_id),
        "normalized_label": normalized_label,
        "action": "escalate",
        "operator": operator,
    }


# ============================================================
# Internal — admin action audit row
# ============================================================


_ADMIN_EVENT_BY_ACTION = {
    "resolve": "admin_name_resolution_resolve",
    "escalate": "admin_name_resolution_escalate",
    "reject_challenges": "admin_name_resolution_reject_challenges",
}


def _emit_admin_action_event(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
    action: str,
    operator: str,
    operator_note: str | None,
    extra: dict[str, Any],
) -> None:
    """INSERT a ``phase='manual'`` audit row for the admin operator action.

    Distinct from the ``consensus_state_changed`` event the ledger may
    emit — this row captures the operator intent (handle + note) so a
    no-op resolve (e.g. re-affirming a verified EAN) still leaves a
    paper trail.

    Best-effort : a failed insert never gates the user-facing mutation,
    matching the pattern in :mod:`routes.admin.scans._audit`.
    """
    event = _ADMIN_EVENT_BY_ACTION.get(action, f"admin_name_resolution_{action}")
    payload = {
        "operator": operator,
        "operator_note": operator_note,
        "store_id": str(store_id),
        "normalized_label": normalized_label,
        **extra,
    }
    try:
        db.execute(
            text(
                """
                INSERT INTO pipeline_audit_log
                    (phase, level, event, scan_id, parsed_ticket_id, payload)
                VALUES
                    ('manual', 'normal', :event, NULL, NULL,
                     CAST(:payload AS jsonb))
                """
            ),
            {"event": event, "payload": json.dumps(payload)},
        )
        db.flush()
    except Exception:
        logger.warning(
            "audit insert failed (admin nrc action %s) — best-effort skip",
            action,
            exc_info=True,
        )


__all__ = [
    "ADMIN_SUPPORT_ID",
    "LabelNotFound",
    "QueueItem",
    "SampleScan",
    "StateMismatch",
    "TopEan",
    "UnmatchedItem",
    "escalate_label",
    "get_label_detail",
    "list_arbitration_queue",
    "list_unmatched_queue",
    "reject_challenges",
    "resolve_label",
]

# Suppress unused-import warning for User — kept available for callers
# that need the model in nearby modules without re-importing.
_ = User
