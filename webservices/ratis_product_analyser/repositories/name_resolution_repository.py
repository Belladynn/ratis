"""Read-only repository for the name-resolution consensus ledger.

See ``ARCH_name_resolution_consensus.md`` (parent) and
``ARCH_cross_retailer_consensus.md`` (Bloc B contract — this file) for
the full contract.

Bloc B (cross-retailer) scope :

- ``get_consensus_for_label`` — compute live consensus state for a
  ``(retailer_id, source_type, normalized_label)`` triple. **Signature
  changed from Bloc A** : the aggregation key swapped from ``store_id``
  to ``retailer_id`` so consensus aggregates across all stores of a
  retailer chain. Source type (``'receipt'`` vs ``'esl'``) keeps the
  two ledgers logically separate.
- ``find_fuzzy_verified_consensus`` — pg_trgm fuzzy fallback, retailer-
  wide. Uses the partial GIN trgm index ``idx_pnr_norm_label_trgm``
  (Bloc A migration) ; gates on ``similarity >= sim_min`` and
  ``abs(len_diff) <= len_diff_max`` from settings.
- ``was_ever_verified`` — audit-log probe keyed by ``retailer_id`` +
  ``source_type``.
- ``list_divergent_labels`` / ``list_unmatched_labels`` — admin queues,
  grouped by ``(retailer_id, source_type, normalized_label)``.

Transitional API : ``get_consensus_for_label_by_store`` and
``find_fuzzy_verified_consensus_by_store`` resolve ``retailer_id`` from
``store_id`` and forward to the canonical functions with
``source_type='receipt'``. They exist so Bloc C (matcher cascade) and
Bloc D / F (worker tasks + admin service) can migrate at their own
pace ; the wrappers will be removed when the last caller is gone.

Write paths (``record_resolution``, audit emission) live in
``name_resolution_writes.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from ratis_core.models.name_resolution import ProductNameResolution
from ratis_core.models.scan import Scan
from ratis_core.models.store import Store
from ratis_core.settings import load_settings
from sqlalchemy import and_, func, literal, select, text
from sqlalchemy.orm import Session

from repositories.consensus_state import ConsensusState
from repositories.retailer_resolution import resolve_retailer_id

# ============================================================
# Public types
# ============================================================


@dataclass(frozen=True)
class ConsensusResult:
    """Outcome of a live consensus computation for a
    ``(retailer_id, source_type, normalized_label)`` triple.

    Attributes
    ----------
    ean
        The product EAN with the highest weighted vote count (top1).
    distinct_validators
        Count of distinct ``user_id`` having submitted a *contributing*
        validation (method in ``validation_methods`` setting).
    top1_pct
        Share of total weighted votes captured by ``ean`` (0–100).
    state
        Derived ``ConsensusState`` (see ``consensus_state.py``).
    """

    ean: str
    distinct_validators: int
    top1_pct: float
    state: ConsensusState


@dataclass(frozen=True)
class DivergentLabelRow:
    """Row returned by :func:`list_divergent_labels` for the admin queue.

    Bloc B grouping : ``(retailer_id, source_type, normalized_label)``.
    """

    retailer_id: uuid.UUID
    source_type: str
    normalized_label: str
    distinct_validators: int
    top1_ean: str
    top1_pct: float


@dataclass(frozen=True)
class UnmatchedLabelRow:
    """Row returned by :func:`list_unmatched_labels` for the admin queue."""

    retailer_id: uuid.UUID
    normalized_label: str
    scan_count: int


# ============================================================
# Internals
# ============================================================


def _consensus_settings() -> dict:
    """Load the ``name_resolution_consensus`` section, fail-fast if absent."""
    return load_settings()["name_resolution_consensus"]


def _validation_methods_for(settings: dict, source_type: str) -> list[str]:
    """Return the contributing validation methods for ``source_type``.

    Bloc A added per-source method whitelists
    (``validation_methods_receipt`` / ``validation_methods_esl``) to
    keep the receipt and ESL ledgers logically independent. Falls back
    to the unified ``validation_methods`` key when the source-specific
    key is absent (backward-compat with the alpha settings shape).
    """
    key = f"validation_methods_{source_type}"
    if key in settings:
        return list(settings[key])
    return list(settings["validation_methods"])


def _case(*cases, else_):
    """SQLAlchemy 2.0 ``case`` shim — keeps imports tidy at call sites."""
    from sqlalchemy import case as sa_case

    return sa_case(*cases, else_=else_)


def _evaluate(
    *,
    distinct_validators: int,
    top1_weight: int,
    top2_weight: int,
    total_weight: int,
    top1_ean: str,
    settings: dict,
    was_ever_verified: bool = False,
) -> ConsensusResult:
    """Derive the ``ConsensusState`` from raw counts.

    See :class:`ConsensusState` for the full state taxonomy. This
    function stays pure (no DB access) so the audit-log probe can be
    injected by the caller via ``was_ever_verified``.
    """
    min_users = int(settings["min_distinct_users"])
    convergence_pct = float(settings["convergence_threshold_pct"])
    lead_factor = float(settings["min_top1_lead_factor"])

    top1_pct = (top1_weight / total_weight * 100.0) if total_weight else 0.0
    quorum = distinct_validators >= min_users

    if not quorum:
        return ConsensusResult(
            ean=top1_ean,
            distinct_validators=distinct_validators,
            top1_pct=top1_pct,
            state=ConsensusState.PENDING,
        )

    pct_ok = top1_pct >= convergence_pct
    lead_ok = (top2_weight == 0) or (top1_weight >= lead_factor * top2_weight)

    if pct_ok and lead_ok:
        state = ConsensusState.VERIFIED
    elif was_ever_verified:
        state = ConsensusState.UNVERIFIED
    else:
        state = ConsensusState.CONTROVERSE

    return ConsensusResult(
        ean=top1_ean,
        distinct_validators=distinct_validators,
        top1_pct=top1_pct,
        state=state,
    )


def was_ever_verified(
    db: Session,
    *,
    retailer_id: uuid.UUID,
    source_type: str,
    normalized_label: str,
) -> bool:
    """Return True iff ``(retailer_id, source_type, normalized_label)``
    has been promoted to ``VERIFIED`` at any point in the past.

    Reads ``pipeline_audit_log`` for any ``consensus_state_changed`` event
    whose payload encodes the matching ``retailer_id`` + ``source_type`` +
    ``normalized_label`` and ``to_state='verified'``. The append-only
    nature of the audit log guarantees a once-verified triple stays
    detectable forever — even after a transition back to a divergent
    state (drives the ``UNVERIFIED`` post-promotion-fall semantics).

    Bloc B note : the payload schema gains ``retailer_id`` +
    ``source_type`` keys (Bloc C will write them). Alpha rows that only
    carry ``store_id`` no longer match this filter — by design, since
    the alpha consensus was per-store and didn't cross retailer
    boundaries.

    Backed by the partial index ``idx_pal_consensus_state_changed``
    (migration ``20260501_1700_nrcC``) so the lookup remains O(log n).
    """
    row = db.execute(
        text(
            """
            SELECT 1 FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'retailer_id' = :retailer_id
              AND payload->>'source_type' = :source_type
              AND payload->>'normalized_label' = :label
              AND payload->>'to_state' = 'verified'
            LIMIT 1
            """
        ),
        {
            "retailer_id": str(retailer_id),
            "source_type": source_type,
            "label": normalized_label,
        },
    ).first()
    return row is not None


# ============================================================
# Public reads — retailer-keyed canonical signatures
# ============================================================


def get_consensus_for_label(
    db: Session,
    *,
    retailer_id: uuid.UUID,
    source_type: str,
    normalized_label: str,
    was_ever_verified_override: bool | None = None,
) -> ConsensusResult | None:
    """Compute the live consensus state for a
    ``(retailer_id, source_type, normalized_label)`` triple.

    Bloc B contract :

    - Aggregation is retailer-wide : votes from all stores of the
      retailer chain pool together. A user voting from store A and
      store B counts once via ``COUNT(DISTINCT user_id)``.
    - ``source_type`` (``'receipt'`` / ``'esl'``) keeps the two ledgers
      independent — the matcher cascade decides which to consult, and
      the cross-source stage 7a queries them separately.
    - Rows with ``retailer_id IS NULL`` (user-suggested unvalidated
      stores) are excluded — they're out of the consensus path and
      stay in the store-validation track.

    Returns ``None`` when no contributing validation row exists for the
    triple (semantically ``UNRESOLVED`` ; the caller maps as needed).
    Otherwise returns a :class:`ConsensusResult` carrying the leader
    EAN and derived state per
    ``settings.name_resolution_consensus``.
    """
    settings = _consensus_settings()
    methods = _validation_methods_for(settings, source_type)
    if not methods:
        return None

    # Per-EAN aggregation : weighted vote count + distinct user count.
    # Anti-fraud V1 : ``weight_override`` (when not NULL) replaces the
    # method-derived weight. Currently only ``0`` is written for shadow-
    # banned users — their rows stay for audit but contribute zero to
    # the consensus.
    method_weight_expr = _case(
        (ProductNameResolution.match_method == "manual_admin", literal(int(settings["admin_validation_weight"]))),
        *((ProductNameResolution.match_method == m, literal(1)) for m in methods if m != "manual_admin"),
        else_=literal(0),
    )
    weight_expr = func.coalesce(ProductNameResolution.weight_override, method_weight_expr)

    rows = db.execute(
        select(
            ProductNameResolution.product_ean,
            func.sum(weight_expr).label("weight"),
            func.count(
                func.distinct(
                    _case(
                        (weight_expr == 0, literal(None)),
                        else_=ProductNameResolution.user_id,
                    )
                )
            ).label("distinct_users"),
        )
        .where(
            ProductNameResolution.retailer_id == retailer_id,
            ProductNameResolution.source_type == source_type,
            ProductNameResolution.normalized_label == normalized_label,
            ProductNameResolution.match_method.in_(methods),
        )
        .group_by(ProductNameResolution.product_ean)
        .order_by(func.sum(weight_expr).desc())
    ).all()

    if not rows:
        return None

    # Distinct validators across all EANs (a user voting on multiple
    # EANs in different stores of the retailer counts once for quorum).
    distinct_validators = db.execute(
        select(
            func.count(
                func.distinct(
                    _case(
                        (weight_expr == 0, literal(None)),
                        else_=ProductNameResolution.user_id,
                    )
                )
            )
        ).where(
            ProductNameResolution.retailer_id == retailer_id,
            ProductNameResolution.source_type == source_type,
            ProductNameResolution.normalized_label == normalized_label,
            ProductNameResolution.match_method.in_(methods),
        )
    ).scalar_one()

    top1 = rows[0]
    top1_weight = int(top1.weight or 0)
    top2_weight = int(rows[1].weight) if len(rows) > 1 and rows[1].weight else 0
    total_weight = sum(int(r.weight or 0) for r in rows)

    if total_weight == 0:
        return None

    if was_ever_verified_override is None:
        verified_history = was_ever_verified(
            db,
            retailer_id=retailer_id,
            source_type=source_type,
            normalized_label=normalized_label,
        )
    else:
        verified_history = was_ever_verified_override

    return _evaluate(
        distinct_validators=int(distinct_validators or 0),
        top1_weight=top1_weight,
        top2_weight=top2_weight,
        total_weight=total_weight,
        top1_ean=str(top1.product_ean),
        settings=settings,
        was_ever_verified=verified_history,
    )


def find_fuzzy_verified_consensus(
    db: Session,
    *,
    retailer_id: uuid.UUID,
    source_type: str,
    cleaned_label: str,
    max_len_diff: int | None = None,
    min_similarity: float | None = None,
) -> ConsensusResult | None:
    """Find a ``VERIFIED`` consensus for a label fuzzy-close to
    ``cleaned_label`` within the same ``(retailer_id, source_type)``.

    Bloc B contract :

    - Retailer-wide : the SQL filter pulls candidates across all stores
      of the retailer.
    - Source-type isolated : a verified ESL label cannot satisfy a
      receipt query (and vice versa). Cross-source matching is the
      stage 7a job (Bloc C), not this fuzzy path.
    - Strict gates from settings (``fuzzy_consensus_*``, falls back to
      legacy ``fuzzy_label_*`` keys for backward-compat) :
      ``similarity >= min_similarity`` and
      ``abs(length(label) - length(query)) <= max_len_diff``.
    - Only ``VERIFIED`` consensus is returned ; ``PENDING`` /
      ``CONTROVERSE`` / ``UNVERIFIED`` candidates are skipped.

    Empirical thresholds (validated 2026-05-02 on real OCR data) : OCR
    variants score 0.85+ ; real product variants (vanille vs fraise)
    score 0.60-. The default 0.80 leaves a comfortable gap.

    Backed by the partial GIN trgm index ``idx_pnr_norm_label_trgm``
    (Bloc A migration). The B-tree partial index
    ``idx_pnr_retailer_source_label`` covers the equality filters.
    """
    settings = _consensus_settings()
    if max_len_diff is None:
        max_len_diff = int(
            settings.get(
                "fuzzy_consensus_len_diff_max",
                settings.get("fuzzy_label_max_len_diff", 2),
            )
        )
    if min_similarity is None:
        min_similarity = float(
            settings.get(
                "fuzzy_consensus_similarity_min",
                settings.get("fuzzy_label_min_similarity", 0.80),
            )
        )

    # Pull up to 5 fuzzy-close labels (DISTINCT, exact match excluded).
    # The B-tree partial index ``idx_pnr_retailer_source_label`` covers
    # the equality filters ; the GIN trgm partial index
    # ``idx_pnr_norm_label_trgm`` (where retailer_id IS NOT NULL)
    # accelerates the similarity probe.
    rows = db.execute(
        text(
            """
            SELECT DISTINCT pnr.normalized_label,
                   similarity(pnr.normalized_label, :cleaned_label) AS sim
            FROM product_name_resolutions pnr
            WHERE pnr.retailer_id = :retailer_id
              AND pnr.source_type = :source_type
              AND pnr.normalized_label != :cleaned_label
              AND ABS(LENGTH(pnr.normalized_label) - LENGTH(:cleaned_label))
                  <= :max_len_diff
              AND similarity(pnr.normalized_label, :cleaned_label)
                  >= :min_similarity
            ORDER BY sim DESC
            LIMIT 5
            """
        ),
        {
            "retailer_id": str(retailer_id),
            "source_type": source_type,
            "cleaned_label": cleaned_label,
            "max_len_diff": max_len_diff,
            "min_similarity": min_similarity,
        },
    ).fetchall()

    for row in rows:
        consensus = get_consensus_for_label(
            db,
            retailer_id=retailer_id,
            source_type=source_type,
            normalized_label=row.normalized_label,
        )
        if consensus is not None and consensus.state == ConsensusState.VERIFIED:
            return consensus

    return None


def get_consensus_states_for_scans(
    db: Session,
    *,
    scan_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ConsensusState]:
    """Return the live ``ConsensusState`` for each scan that has at least
    one ``product_name_resolutions`` row.

    The result maps ``scan_id → ConsensusState``. Scans without any
    contributing ledger row (``UNRESOLVED`` semantics) are simply
    absent from the dict — callers should treat ``None`` as "no badge
    to show".

    Bloc B note : the consensus aggregation is now retailer-keyed, so
    the per-scan probe joins the ledger row's ``retailer_id`` +
    ``source_type`` (denormalised by the Bloc A trigger) and queries
    the canonical retailer-keyed function. Scans whose ledger row has
    ``retailer_id IS NULL`` (detached store) are left out of the dict.

    Implementation notes
    --------------------
    - One SQL round-trip resolves ``scan_id → (retailer_id, source_type,
      normalized_label)``. ``DISTINCT ON (scan_id)`` keeps a single
      representative per scan deterministically.
    - The consensus computation itself stays N+1 ; promote to a single
      window-function SQL when V1 alpha volumes outgrow this.
    """
    if not scan_ids:
        return {}

    rows = (
        db.execute(
            text(
                """
            SELECT DISTINCT ON (scan_id)
                scan_id, retailer_id, source_type, normalized_label
            FROM product_name_resolutions
            WHERE scan_id = ANY(:scan_ids)
              AND retailer_id IS NOT NULL
            ORDER BY scan_id, id
            """
            ),
            {"scan_ids": [str(s) for s in scan_ids]},
        )
        .mappings()
        .all()
    )

    out: dict[uuid.UUID, ConsensusState] = {}
    for row in rows:
        scan_id = row["scan_id"]
        if not isinstance(scan_id, uuid.UUID):
            scan_id = uuid.UUID(str(scan_id))
        retailer_id = row["retailer_id"]
        if not isinstance(retailer_id, uuid.UUID):
            retailer_id = uuid.UUID(str(retailer_id))
        result = get_consensus_for_label(
            db,
            retailer_id=retailer_id,
            source_type=row["source_type"],
            normalized_label=row["normalized_label"],
        )
        if result is not None:
            out[scan_id] = result.state
    return out


def list_divergent_labels(
    db: Session,
    *,
    limit: int,
    offset: int,
) -> list[DivergentLabelRow]:
    """Return ``(retailer_id, source_type, normalized_label)`` triples that
    have reached quorum but failed convergence.

    A triple is divergent iff
    ``distinct_validators >= min_distinct_users`` AND it does not satisfy
    both the ``convergence_threshold_pct`` and ``min_top1_lead_factor``
    checks.

    Filtering happens in Python — at NRC's V1 alpha scale this is
    materially cheaper than a window-function SQL pipeline and reuses
    the canonical :func:`get_consensus_for_label`. Promote to SQL when
    volumes demand it.
    """
    settings = _consensus_settings()
    # Pre-filter accepts ANY method that contributes for ANY source_type
    # so we don't miss ESL-only divergences. The per-row evaluation in
    # ``get_consensus_for_label`` re-applies the source-specific filter.
    methods = sorted(set(_validation_methods_for(settings, "receipt")) | set(_validation_methods_for(settings, "esl")))
    min_users = int(settings["min_distinct_users"])
    if not methods:
        return []

    # Step 1 : SQL pre-filter — triples with quorum, retailer-attached.
    candidate_triples = db.execute(
        select(
            ProductNameResolution.retailer_id,
            ProductNameResolution.source_type,
            ProductNameResolution.normalized_label,
        )
        .where(
            ProductNameResolution.match_method.in_(methods),
            ProductNameResolution.retailer_id.is_not(None),
        )
        .group_by(
            ProductNameResolution.retailer_id,
            ProductNameResolution.source_type,
            ProductNameResolution.normalized_label,
        )
        .having(func.count(func.distinct(ProductNameResolution.user_id)) >= min_users)
        .order_by(
            ProductNameResolution.retailer_id,
            ProductNameResolution.source_type,
            ProductNameResolution.normalized_label,
        )
    ).all()

    divergent_states = {ConsensusState.CONTROVERSE, ConsensusState.UNVERIFIED}
    divergent: list[DivergentLabelRow] = []
    for retailer_id, source_type, label in candidate_triples:
        result = get_consensus_for_label(
            db,
            retailer_id=retailer_id,
            source_type=source_type,
            normalized_label=label,
        )
        if result is None or result.state not in divergent_states:
            continue
        divergent.append(
            DivergentLabelRow(
                retailer_id=retailer_id,
                source_type=source_type,
                normalized_label=label,
                distinct_validators=result.distinct_validators,
                top1_ean=result.ean,
                top1_pct=result.top1_pct,
            )
        )

    return divergent[offset : offset + limit]


def list_unmatched_labels(
    db: Session,
    *,
    limit: int,
    offset: int,
) -> list[UnmatchedLabelRow]:
    """Return scanned labels with no consensus row in the ledger.

    Joins ``scans`` against ``stores`` (for ``retailer_id``) and
    ``product_name_resolutions`` to find the triples that the matcher
    could not resolve via crowdsourced consensus.

    Bloc B grouping : ``(retailer_id, scanned_name)``. Scans whose
    store has no resolved ``retailer_id`` (user-suggested pending
    validation, or detached) are excluded — they belong to the store-
    validation track, not the NRC queue.
    """
    has_resolution = (
        select(literal(1))
        .where(
            and_(
                ProductNameResolution.retailer_id == Store.retailer_id,
                ProductNameResolution.normalized_label == Scan.scanned_name,
            )
        )
        .exists()
    )

    rows = db.execute(
        select(
            Store.retailer_id.label("retailer_id"),
            Scan.scanned_name.label("normalized_label"),
            func.count(Scan.id).label("scan_count"),
        )
        .join(Store, Store.id == Scan.store_id)
        .where(
            Scan.store_id.is_not(None),
            Scan.scanned_name.is_not(None),
            Store.retailer_id.is_not(None),
            ~has_resolution,
        )
        .group_by(Store.retailer_id, Scan.scanned_name)
        .order_by(Store.retailer_id, Scan.scanned_name)
        .limit(limit)
        .offset(offset)
    ).all()

    return [
        UnmatchedLabelRow(
            retailer_id=r.retailer_id,
            normalized_label=r.normalized_label,
            scan_count=int(r.scan_count),
        )
        for r in rows
    ]


# ============================================================
# Transitional store-keyed shims
# ============================================================
#
# Bloc B keeps the canonical signatures retailer-keyed. The shims below
# resolve ``retailer_id`` from ``store_id`` and forward to the canonical
# functions with ``source_type='receipt'`` (the only source the legacy
# matcher knew about). They exist so Bloc C/D/F can migrate at their
# own pace ; remove when the last caller is gone.
#
# Behaviour when the store has no ``retailer_id`` (user-suggested
# unvalidated, or non-existent store id) : return ``None``. The
# matcher's legacy fallback path will treat that as UNRESOLVED.


def get_consensus_for_label_by_store(
    db: Session,
    *,
    store_id: uuid.UUID,
    normalized_label: str,
) -> ConsensusResult | None:
    """Deprecated transitional wrapper — use
    :func:`get_consensus_for_label` (retailer-keyed) directly.

    Behaviour :

    1. Resolve ``retailer_id`` from ``store_id``. When present, forward
       to the canonical retailer-keyed function with
       ``source_type='receipt'``.
    2. When the store has no ``retailer_id`` (legacy data, user-
       suggested unvalidated store, or test fixtures that pre-date
       Bloc B), fall back to a ``store_id``-keyed live computation.
       This preserves alpha test semantics during the transition ;
       Bloc C/D/F will migrate the call sites to the canonical and the
       fallback becomes dead code.

    The ``was_ever_verified`` probe used for the post-promotion-fall
    detection is computed via :func:`was_ever_verified_by_store` so
    legacy alpha audit rows (which carry ``store_id`` only, no
    ``retailer_id``) keep their UNVERIFIED semantics during the
    transition.
    """
    retailer_id = resolve_retailer_id(db, store_id)
    if retailer_id is not None:
        return get_consensus_for_label(
            db,
            retailer_id=retailer_id,
            source_type="receipt",
            normalized_label=normalized_label,
            was_ever_verified_override=was_ever_verified_by_store(db, store_id, normalized_label),
        )

    # Legacy store-keyed path — kept transitional for callers that still
    # pass detached stores (alpha data, user-suggested pending). Bloc B
    # canonicals require a retailer_id, so we re-implement the live
    # computation against ``store_id`` here.
    settings = _consensus_settings()
    methods = list(settings["validation_methods"])
    if not methods:
        return None

    method_weight_expr = _case(
        (ProductNameResolution.match_method == "manual_admin", literal(int(settings["admin_validation_weight"]))),
        *((ProductNameResolution.match_method == m, literal(1)) for m in methods if m != "manual_admin"),
        else_=literal(0),
    )
    weight_expr = func.coalesce(ProductNameResolution.weight_override, method_weight_expr)

    rows = db.execute(
        select(
            ProductNameResolution.product_ean,
            func.sum(weight_expr).label("weight"),
            func.count(
                func.distinct(
                    _case(
                        (weight_expr == 0, literal(None)),
                        else_=ProductNameResolution.user_id,
                    )
                )
            ).label("distinct_users"),
        )
        .where(
            ProductNameResolution.store_id == store_id,
            ProductNameResolution.normalized_label == normalized_label,
            ProductNameResolution.match_method.in_(methods),
        )
        .group_by(ProductNameResolution.product_ean)
        .order_by(func.sum(weight_expr).desc())
    ).all()

    if not rows:
        return None

    distinct_validators = db.execute(
        select(
            func.count(
                func.distinct(
                    _case(
                        (weight_expr == 0, literal(None)),
                        else_=ProductNameResolution.user_id,
                    )
                )
            )
        ).where(
            ProductNameResolution.store_id == store_id,
            ProductNameResolution.normalized_label == normalized_label,
            ProductNameResolution.match_method.in_(methods),
        )
    ).scalar_one()

    top1 = rows[0]
    top1_weight = int(top1.weight or 0)
    top2_weight = int(rows[1].weight) if len(rows) > 1 and rows[1].weight else 0
    total_weight = sum(int(r.weight or 0) for r in rows)

    if total_weight == 0:
        return None

    return _evaluate(
        distinct_validators=int(distinct_validators or 0),
        top1_weight=top1_weight,
        top2_weight=top2_weight,
        total_weight=total_weight,
        top1_ean=str(top1.product_ean),
        settings=settings,
        was_ever_verified=was_ever_verified_by_store(db, store_id, normalized_label),
    )


def find_fuzzy_verified_consensus_by_store(
    db: Session,
    *,
    store_id: uuid.UUID,
    cleaned_label: str,
    max_len_diff: int | None = None,
    min_similarity: float | None = None,
) -> ConsensusResult | None:
    """Deprecated transitional wrapper — use
    :func:`find_fuzzy_verified_consensus` (retailer-keyed) directly.

    Behaviour :

    1. Resolve ``retailer_id``. When present, forward to the canonical
       retailer-keyed fuzzy lookup with ``source_type='receipt'``.
    2. When no ``retailer_id`` resolves, fall back to a ``store_id``-
       keyed fuzzy probe so legacy alpha tests/data keep working until
       Bloc C/D/F migrate.
    """
    retailer_id = resolve_retailer_id(db, store_id)
    if retailer_id is not None:
        return find_fuzzy_verified_consensus(
            db,
            retailer_id=retailer_id,
            source_type="receipt",
            cleaned_label=cleaned_label,
            max_len_diff=max_len_diff,
            min_similarity=min_similarity,
        )

    # Legacy store-keyed fuzzy fallback.
    settings = _consensus_settings()
    if max_len_diff is None:
        max_len_diff = int(
            settings.get(
                "fuzzy_consensus_len_diff_max",
                settings.get("fuzzy_label_max_len_diff", 2),
            )
        )
    if min_similarity is None:
        min_similarity = float(
            settings.get(
                "fuzzy_consensus_similarity_min",
                settings.get("fuzzy_label_min_similarity", 0.80),
            )
        )

    rows = db.execute(
        text(
            """
            SELECT DISTINCT pnr.normalized_label,
                   similarity(pnr.normalized_label, :cleaned_label) AS sim
            FROM product_name_resolutions pnr
            WHERE pnr.store_id = :store_id
              AND pnr.normalized_label != :cleaned_label
              AND ABS(LENGTH(pnr.normalized_label) - LENGTH(:cleaned_label))
                  <= :max_len_diff
              AND similarity(pnr.normalized_label, :cleaned_label)
                  > :min_similarity
            ORDER BY sim DESC
            LIMIT 5
            """
        ),
        {
            "store_id": str(store_id),
            "cleaned_label": cleaned_label,
            "max_len_diff": max_len_diff,
            "min_similarity": min_similarity,
        },
    ).fetchall()

    for row in rows:
        consensus = get_consensus_for_label_by_store(db, store_id=store_id, normalized_label=row.normalized_label)
        if consensus is not None and consensus.state == ConsensusState.VERIFIED:
            return consensus

    return None


def was_ever_verified_by_store(
    db: Session,
    store_id: uuid.UUID,
    normalized_label: str,
) -> bool:
    """Deprecated transitional wrapper — use :func:`was_ever_verified`
    (retailer-keyed) directly.

    Resolves ``retailer_id`` from ``store_id`` then forwards with
    ``source_type='receipt'`` and queries the audit log against the
    Bloc B payload schema. Falls back to the legacy ``store_id``
    payload key when no retailer is resolved (alpha audit rows
    pre-Bloc B kept ``store_id`` only) so existing callers don't lose
    their history during the migration.
    """
    retailer_id = resolve_retailer_id(db, store_id)
    if retailer_id is not None and was_ever_verified(
        db,
        retailer_id=retailer_id,
        source_type="receipt",
        normalized_label=normalized_label,
    ):
        return True

    # Legacy fallback : alpha-era audit rows only carry ``store_id`` in
    # their payload. Probe that schema too so transitional callers keep
    # the post-promotion-fall semantics until Bloc C rewrites the audit
    # writer to emit both keys.
    row = db.execute(
        text(
            """
            SELECT 1 FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :store_id
              AND payload->>'normalized_label' = :label
              AND payload->>'to_state' = 'verified'
            LIMIT 1
            """
        ),
        {"store_id": str(store_id), "label": normalized_label},
    ).first()
    return row is not None
