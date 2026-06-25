"""Phase 3 — Matcher (consensus-only refonte 2026-05-02 — pipeline).

NRC Bloc C (cross-retailer consensus, 2026-05-02 — see
``ARCH_cross_retailer_consensus.md`` § "Cascade matcher") swapped the
consensus aggregation key from ``store_id`` to ``retailer_id``. The
matcher now :

1. Resolves ``retailer_id`` from the matched ``store_id`` via the
   :class:`RetailerResolver` callback (typically backed by
   ``repositories.retailer_resolution.resolve_retailer_id``).
2. Calls the consensus exact / fuzzy lookups keyed on ``retailer_id``.
3. Skips the consensus stages entirely when ``retailer_id`` is ``None``
   (user-suggested store pending admin validation, or detached store).

Transforms a :class:`ParsedTicket` (Phase 2) into a
:class:`MatchedTicket` (Pydantic frozen).

For each :class:`ParsedItem`, applies a **cascade of matching**
strictly consensus-only (premier hit gagne) :

  1. **barcode strict** — si ``parsed_item.barcode`` est non-null, lookup
     direct dans ``products`` par EAN. Hit → ``status='matched'``,
     ``match_method='barcode'``, ``match_confidence=1.0``. Miss → on NE
     retombe PAS sur le consensus : un barcode explicite mais inconnu en
     DB est anormal et signale un produit non-référencé
     (``status='unresolved'``, reason ``'barcode_unknown_in_db'``).
  2. **knowledge curated lookup** — si pas de barcode, lookup
     ``ProductKnowledgeLookup`` par ``normalized_label``. Returns a hit
     uniquement quand le mapping est *curated* (admin-validated ou
     auto-promoted via consensus). Hit → matched/knowledge.
  3. **consensus exact** — si toujours rien, lookup le ledger
     ``product_name_resolutions`` pour ``(retailer_id, source_type=
     'receipt', normalized_label)``. Si l'état dérivé est ``VERIFIED``
     → matched/consensus_match avec l'EAN du leader (top1).
  4. **consensus fuzzy** — si la lookup exact rate, fuzzy fallback
     retailer-wide sur le ledger : ``ABS(LENGTH(label) - LENGTH(
     cleaned_label)) <= 2`` et ``similarity > 0.80`` parmi les
     ``VERIFIED`` du retailer. Catch les variantes OCR non-encore
     corrigées par ``ocr_knowledge`` (ex. ``HIPROA BRE SAV FRSE`` →
     consensus de ``HIPRO BRE SAV FRSE``). Hit → matched/consensus_match.
  5. **STOP** — si pas de consensus VERIFIED → ``status='unresolved'``,
     ``product_ean=None``, ``match_method=None``. Pas de fallback fuzzy
     contre ``products`` (philosophie consensus-only). Stage 7a
     (cross-source receipt↔ESL) reste en V2 backlog.

Pour le store : fuzzy match sur header (brand + address + postcode +
city). 3 statuts :

  - ``matched`` : top1 score >= ``store_threshold``
  - ``suggested`` : ``0.5 <= top1.score < store_threshold`` — Phase 4
    créera l'entrée ``store_candidates``
  - ``unresolved`` : aucun candidat ou top1 < 0.5

Pure fonctionnel : tout I/O DB passe par des callbacks ``Protocol``.
L'orchestrator wirera vers la DB ; les tests utilisent des stubs.

Anti-patterns explicitement bannis (cf. ARCH § Anti-patterns) :

- ❌ ``return None`` pour un item ambigu — chaque ParsedItem produit
  exactement un :class:`ItemMatch`.
- ❌ Surface ``top_candidates`` dans l'UI pour que l'user pick.
  ``top_candidates`` est **read-only-internal** (audit, ML futur).
- ❌ Drop silencieux d'un item sans :attr:`ItemMatch.rejected_reason`.
  L'invariant Pydantic le bloque ; ce module l'enforce explicitement.
- ❌ Fuzzy product-name matching contre ``products`` — la philosophie
  consensus-only (refonte 2026-05-02 — cf.
  ``ARCH_name_resolution_consensus.md`` § "Philosophie") interdit
  totalement le matching à coups de noms produits OFF/internal pour
  éviter les faux positifs (ex: 30+ "Hipro" génériques tagués
  aléatoirement).

Cf. ``ARCH_receipt_pipeline.md`` § Phase 3 + § Traçabilité (
``top_candidates`` + ``decision_inputs``) and
``ARCH_name_resolution_consensus.md`` § "Philosophie",
``ARCH_cross_retailer_consensus.md`` § "Cascade matcher".
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from worker.pipeline.extract import AuditLogger, _noop_audit
from worker.pipeline.types import (
    Candidate,
    DecisionInputs,
    ItemMatch,
    MatchedTicket,
    ParsedFooter,
    ParsedHeader,
    ParsedItem,
    ParsedTicket,
    StoreMatchStatus,
)

logger = logging.getLogger(__name__)


# ── Lookup Protocols ──────────────────────────────────────────────────────


class ProductByEanLookup(Protocol):
    """Lookup a product by its exact EAN.

    Implementation typique : ``SELECT ean, name_normalized FROM products
    WHERE ean = :ean``. Returns ``{"ean": str, "label": str}`` on hit,
    ``None`` on miss. The orchestrator wires this to the DB ; tests pass
    a stub.
    """

    def __call__(self, ean: str) -> dict | None: ...


class ProductByKnowledgeLookup(Protocol):
    """Curated knowledge lookup by ``normalized_label``.

    Returns ``{"ean": str, "label": str, "score": float}`` if a
    *curated* mapping exists (admin-validated or auto-promoted through
    a verified consensus), ``None`` otherwise.

    Per the consensus-only refonte (2026-05-02) this lookup must NEVER
    perform speculative fuzzy matching against the ``products`` table.
    Curated lookups are safe (admin-validated) ; speculative ones leak
    OFF noise into user matches.
    """

    def __call__(self, normalized_label: str) -> dict | None: ...


class ConsensusExactLookup(Protocol):
    """Exact lookup of the consensus state for ``(retailer_id, label)``.

    Bloc C contract : the lookup is keyed on ``retailer_id`` (resolved
    from ``store_id`` via :class:`RetailerResolver`). The wiring layer
    pins ``source_type='receipt'`` at the repository call site —
    cross-source matching (ESL ↔ receipt) is a separate stage (V2
    backlog).

    Returns ``{"ean": str, "state": str}`` when a contributing
    validation row exists in ``product_name_resolutions``. The ``state``
    field maps to :class:`repositories.consensus_state.ConsensusState`
    values (``"VERIFIED"`` / ``"PENDING"`` / ``"CONTROVERSE"`` /
    ``"UNVERIFIED"`` / ``"UNRESOLVED"``). The matcher only treats
    ``"VERIFIED"`` as a positive match ; everything else returns
    ``unresolved`` with the consensus state recorded for audit.

    Returns ``None`` when no contributing row exists at all.
    """

    def __call__(self, retailer_id: UUID, normalized_label: str) -> dict | None: ...


class ConsensusFuzzyLookup(Protocol):
    """Fuzzy fallback consensus lookup, retailer-keyed.

    Searches ``product_name_resolutions`` for a ``VERIFIED`` consensus
    on a label fuzzy-close to ``cleaned_label`` (length diff ≤ 2,
    similarity > 0.80) within the same ``(retailer_id, source_type=
    'receipt')``. Returns the matched ``{"ean": str, "label": str,
    "similarity": float}`` on a hit, ``None`` on miss.

    The implementation is expected to enforce the strict gates and
    only return ``VERIFIED`` neighbours — the matcher trusts the
    contract and does NOT re-check.
    """

    def __call__(self, retailer_id: UUID, cleaned_label: str) -> dict | None: ...


class RetailerResolver(Protocol):
    """Resolve a ``store_id`` to its parent ``retailer_id``.

    Bloc C contract : the cross-retailer consensus key is
    ``retailer_id``, denormalised from ``stores.retailer_id``. This
    callback wraps :func:`repositories.retailer_resolution.resolve_retailer_id`
    in the production wiring ; tests pass a closure that returns a
    fixed UUID (or ``None`` to simulate user-suggested stores pending
    admin validation).

    Returns the resolved ``retailer_id`` UUID or ``None`` when :

    - The store exists but ``retailer_id IS NULL`` (user-suggested
      pending validation).
    - The store id does not exist (defensive : avoids a crash on stale
      UUIDs ; the matcher treats this as "skip consensus").
    """

    def __call__(self, store_id: UUID) -> UUID | None: ...


class StoreLookup(Protocol):
    """Fuzzy match a store via header fields.

    Returns a list of dicts ``[{"id": UUID, "name": str, "address":
    str, "score": float}, ...]`` ordered by score DESC, empty list on
    no candidate. Score is in ``[0, 1]``.

    Implementation typique combine ``name_normalized`` fuzzy + postcode
    exact + city contains. The orchestrator wires this to the DB ;
    tests pass a stub.
    """

    def __call__(
        self,
        *,
        brand: str | None,
        address_line: str | None,
        postcode: str | None,
        city: str | None,
        top_n: int = 3,
    ) -> list[dict]: ...


class StoreByCodeLookup(Protocol):
    """Direct lookup of a :class:`Store` by ``(retailer_key, store_code)``.

    The store_code is decoded from the receipt's internal ticket-number
    barcode (cf. :class:`worker.pipeline.types.ParsedReceiptBarcode`).
    When both retailer + code are present, this lookup short-circuits
    the fuzzy header match — a barcode hit is canonical, score=1.0.

    Returns ``{"id": UUID, "name": str, "address": str, "score": 1.0}``
    on a hit, ``None`` on miss (then the matcher falls back to fuzzy).
    """

    def __call__(self, retailer: str, store_code: str) -> dict | None: ...


# ── Default no-op stubs (defensive defaults, used by tests at will) ───────


def _noop_product_ean(ean: str) -> dict | None:
    return None


def _noop_product_knowledge(normalized_label: str) -> dict | None:
    return None


def _noop_consensus_exact(retailer_id: UUID, normalized_label: str) -> dict | None:
    return None


def _noop_consensus_fuzzy(retailer_id: UUID, cleaned_label: str) -> dict | None:
    return None


def _noop_retailer_resolver(store_id: UUID) -> UUID | None:
    """Default retailer resolver : returns ``None`` for any store.

    A no-op resolver causes the cascade to short-circuit at Stage 3
    with ``rejected_reason='no_retailer_for_consensus'``. Production
    callers MUST wire the real resolver
    (:func:`repositories.retailer_resolution.resolve_retailer_id`) ;
    the no-op exists so the test API stays clean (callers that don't
    care about consensus can skip the kwarg).
    """
    return None


def _noop_store_lookup(
    *,
    brand: str | None,
    address_line: str | None,
    postcode: str | None,
    city: str | None,
    top_n: int = 3,
) -> list[dict]:
    return []


def _noop_store_by_code(retailer: str, store_code: str) -> dict | None:
    return None


# ── Tunables (store thresholds — products no longer have any) ─────────────


DEFAULT_STORE_MATCH_THRESHOLD: float = 0.80
"""At or above this similarity, the store is confirmed. Below 0.5 the
store is unresolved. The intermediate band emits ``store_status='suggested'``
so Phase 4 can create a ``store_candidates`` row for admin / consensus
review.
"""

DEFAULT_STORE_SUGGEST_FLOOR: float = 0.50
"""Below this similarity, even ``suggested`` doesn't apply — too noisy
to be worth a candidate row."""

DEFAULT_TOP_N_CANDIDATES: int = 5
"""Capped at the type level (``ItemMatch`` invariant : len <= 5).

Kept for API stability ; the consensus-only cascade emits at most one
candidate per match (the verified leader)."""


# ── Public API ────────────────────────────────────────────────────────────


def match_ticket(
    parsed: ParsedTicket,
    *,
    product_by_ean: ProductByEanLookup = _noop_product_ean,
    product_by_knowledge: ProductByKnowledgeLookup = _noop_product_knowledge,
    consensus_exact: ConsensusExactLookup = _noop_consensus_exact,
    consensus_fuzzy: ConsensusFuzzyLookup = _noop_consensus_fuzzy,
    retailer_resolver: RetailerResolver = _noop_retailer_resolver,
    store_lookup: StoreLookup = _noop_store_lookup,
    store_by_code: StoreByCodeLookup = _noop_store_by_code,
    audit_logger: AuditLogger = _noop_audit,
    log_level: str = "normal",
    store_match_id: UUID | None = None,
    store_threshold: float = DEFAULT_STORE_MATCH_THRESHOLD,
    store_suggest_floor: float = DEFAULT_STORE_SUGGEST_FLOOR,
) -> MatchedTicket:
    """Run Phase 3 on a :class:`ParsedTicket`. Returns a frozen
    :class:`MatchedTicket` with :attr:`MatchedTicket.item_matches`
    populated 1-to-1 with ``parsed.items`` (no drops, no re-ordering)
    plus the store match outcome.

    Args:
        parsed: Phase 2 output.
        product_by_ean: barcode → product lookup.
        product_by_knowledge: curated knowledge → product lookup.
        consensus_exact: ``(retailer_id, label) → consensus`` lookup
            (Bloc C : retailer-keyed). Wiring layer pins
            ``source_type='receipt'`` at the repository call site.
        consensus_fuzzy: fuzzy-fallback consensus lookup, retailer-
            keyed (Bloc C).
        retailer_resolver: ``store_id → retailer_id`` callback. Bloc C
            cross-retailer consensus key. Returns ``None`` for user-
            suggested stores pending admin validation, in which case
            the cascade short-circuits at Stage 3 with
            ``rejected_reason='no_retailer_for_consensus'``.
        store_lookup / store_by_code: store match callbacks.
        audit_logger: callback for ``pipeline_audit_log`` events.
        log_level: ``"verbose"`` / ``"normal"`` / ``"production"`` —
            controls per-item event emission.
        store_match_id: pre-computed store id when the caller has
            already run store match. When ``None`` (default), this
            function runs :func:`_match_store` itself ; consensus
            lookups only fire when a store match exists AND the
            retailer resolver returns a non-NULL retailer_id.
        store_threshold / store_suggest_floor: tunables for the store
            cascade.

    Returns:
        :class:`MatchedTicket` — frozen, ready for Phase 4 persistence.

    Notes:
        Pure functional ; no I/O. The caller is responsible for
        wiring lookups to the DB.
    """
    if store_suggest_floor > store_threshold:
        raise ValueError(
            f"store_suggest_floor ({store_suggest_floor}) cannot exceed store_threshold ({store_threshold})."
        )

    audit_logger(
        phase="match",
        level="normal",
        event="match_started",
        payload={
            "parsed_ticket_id": str(parsed.id),
            "receipt_id": str(parsed.receipt_id),
            "item_count": len(parsed.items),
        },
    )

    # Store match runs FIRST so item-level consensus lookups can be
    # keyed on the resolved store_id → retailer_id pair. Tests can
    # short-circuit by passing a pre-computed ``store_match_id``.
    if store_match_id is None:
        store_status, resolved_store_id, store_reason = _match_store(
            parsed.header,
            footer=parsed.footer,
            store_lookup=store_lookup,
            store_by_code=store_by_code,
            audit_logger=audit_logger,
            log_level=log_level,
            threshold=store_threshold,
            suggest_floor=store_suggest_floor,
        )
    else:
        # Caller already ran store match (or pinned the value in a
        # test) — reuse it. We still emit a single audit event so
        # observability stays consistent.
        store_status, resolved_store_id, store_reason = (
            "matched",
            store_match_id,
            None,
        )

    # Bloc C : resolve retailer_id once per ticket (same store applies
    # to every item). When the store is unresolved or has no retailer,
    # ``retailer_id`` stays None and per-item logic short-circuits the
    # consensus stages with the appropriate rejected_reason.
    resolved_retailer_id: UUID | None = None
    if resolved_store_id is not None:
        resolved_retailer_id = retailer_resolver(resolved_store_id)
        if log_level == "verbose":
            audit_logger(
                phase="match",
                level="verbose",
                event="retailer_resolved" if resolved_retailer_id else "retailer_unresolved",
                payload={
                    "store_id": str(resolved_store_id),
                    "retailer_id": str(resolved_retailer_id) if resolved_retailer_id else None,
                },
            )

    item_matches = tuple(
        _match_one_item(
            item,
            store_id=resolved_store_id,
            retailer_id=resolved_retailer_id,
            product_by_ean=product_by_ean,
            product_by_knowledge=product_by_knowledge,
            consensus_exact=consensus_exact,
            consensus_fuzzy=consensus_fuzzy,
            audit_logger=audit_logger,
            log_level=log_level,
        )
        for item in parsed.items
    )

    matched = MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=item_matches,
        store_match_id=resolved_store_id,
        store_status=store_status,
        store_rejected_reason=store_reason,
    )

    audit_logger(
        phase="match",
        level="normal",
        event="match_completed",
        payload={
            "parsed_ticket_id": str(parsed.id),
            "matched_count": sum(1 for m in item_matches if m.status == "matched"),
            "unresolved_count": sum(1 for m in item_matches if m.status == "unresolved"),
            "rejected_count": sum(1 for m in item_matches if m.status == "rejected"),
            "store_status": store_status,
        },
    )
    return matched


# ── Helpers privés — items ────────────────────────────────────────────────


def _match_one_item(
    item: ParsedItem,
    *,
    store_id: UUID | None,
    retailer_id: UUID | None,
    product_by_ean: ProductByEanLookup,
    product_by_knowledge: ProductByKnowledgeLookup,
    consensus_exact: ConsensusExactLookup,
    consensus_fuzzy: ConsensusFuzzyLookup,
    audit_logger: AuditLogger,
    log_level: str,
) -> ItemMatch:
    """Cascade : barcode → knowledge → consensus exact → consensus fuzzy.

    Bloc C : the consensus stages key on ``retailer_id`` (resolved by
    the caller from ``store_id`` via :class:`RetailerResolver`). When
    ``retailer_id`` is ``None`` (user-suggested store pending admin
    validation, or detached) the cascade short-circuits with
    ``rejected_reason='no_retailer_for_consensus'``.

    Always returns an :class:`ItemMatch` — never ``None``, never
    raises (except on lookup callback errors which propagate as
    :class:`MatchError` from the caller). All non-matched outcomes
    populate :attr:`ItemMatch.rejected_reason` (Pydantic invariant
    enforces it).
    """
    # 1. Barcode strict — explicit barcode, no fallback on miss.
    if item.barcode:
        prod = product_by_ean(item.barcode)
        if prod is not None:
            candidate = Candidate(
                product_ean=prod["ean"],
                product_label=prod["label"],
                score=1.0,
                source="barcode",
            )
            decision = DecisionInputs(
                normalized_label=item.normalized_label,
                barcode_used=item.barcode,
                knowledge_lookup_hit=False,
                consensus_state=None,
                candidates_considered=1,
            )
            match = ItemMatch(
                parsed_item_id=item.id,
                status="matched",
                product_ean=prod["ean"],
                match_method="barcode",
                match_confidence=1.0,
                rejected_reason=None,
                top_candidates=(candidate,),
                decision_inputs=decision,
            )
            _emit_item_event(audit_logger, log_level, item, match, "matched_barcode")
            return match
        # Barcode set but unknown in DB — anomaly, no fallback.
        decision = DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=item.barcode,
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=0,
        )
        match = ItemMatch(
            parsed_item_id=item.id,
            status="unresolved",
            product_ean=None,
            match_method=None,
            match_confidence=None,
            rejected_reason="barcode_unknown_in_db",
            top_candidates=(),
            decision_inputs=decision,
        )
        _emit_item_event(audit_logger, log_level, item, match, "unresolved_barcode_unknown")
        return match

    # 2. Curated knowledge lookup — admin-validated or auto-promoted.
    knowledge = product_by_knowledge(item.normalized_label)
    if knowledge is not None:
        knowledge_score = float(knowledge.get("score", 0.95))
        candidate = Candidate(
            product_ean=knowledge["ean"],
            product_label=knowledge["label"],
            score=knowledge_score,
            source="knowledge",
        )
        decision = DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=True,
            consensus_state=None,
            candidates_considered=1,
        )
        match = ItemMatch(
            parsed_item_id=item.id,
            status="matched",
            product_ean=knowledge["ean"],
            match_method="knowledge",
            match_confidence=knowledge_score,
            rejected_reason=None,
            top_candidates=(candidate,),
            decision_inputs=decision,
        )
        _emit_item_event(audit_logger, log_level, item, match, "matched_knowledge")
        return match

    # 3. Consensus lookups require a store_id AND a resolved retailer_id
    # (Bloc C : the ledger is keyed on retailer_id ; user-suggested
    # stores have retailer_id=NULL and are out of the consensus path).
    if store_id is None:
        decision = DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=0,
        )
        match = ItemMatch(
            parsed_item_id=item.id,
            status="unresolved",
            product_ean=None,
            match_method=None,
            match_confidence=None,
            rejected_reason="no_store_for_consensus",
            top_candidates=(),
            decision_inputs=decision,
        )
        _emit_item_event(audit_logger, log_level, item, match, "unresolved_no_store_for_consensus")
        return match

    if retailer_id is None:
        decision = DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=0,
        )
        match = ItemMatch(
            parsed_item_id=item.id,
            status="unresolved",
            product_ean=None,
            match_method=None,
            match_confidence=None,
            rejected_reason="no_retailer_for_consensus",
            top_candidates=(),
            decision_inputs=decision,
        )
        _emit_item_event(audit_logger, log_level, item, match, "unresolved_no_retailer_for_consensus")
        return match

    # 3a. Consensus exact lookup — retailer-keyed (Bloc C).
    consensus = consensus_exact(retailer_id, item.normalized_label)
    if consensus is not None and consensus.get("state") == "VERIFIED":
        candidate = Candidate(
            product_ean=consensus["ean"],
            product_label=item.normalized_label,
            score=1.0,
            source="consensus_match",
        )
        decision = DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state="VERIFIED",
            candidates_considered=1,
        )
        match = ItemMatch(
            parsed_item_id=item.id,
            status="matched",
            product_ean=consensus["ean"],
            match_method="consensus_match",
            match_confidence=1.0,
            rejected_reason=None,
            top_candidates=(candidate,),
            decision_inputs=decision,
        )
        _emit_item_event(audit_logger, log_level, item, match, "matched_consensus_exact")
        return match

    # 3b. Fuzzy fallback against VERIFIED consensus neighbours, retailer-
    # wide (Bloc C — pg_trgm runs across every store of the retailer).
    fuzzy = consensus_fuzzy(retailer_id, item.normalized_label)
    if fuzzy is not None:
        candidate = Candidate(
            product_ean=fuzzy["ean"],
            product_label=fuzzy.get("label", item.normalized_label),
            score=float(fuzzy.get("similarity", 1.0)),
            source="consensus_match",
        )
        decision = DecisionInputs(
            normalized_label=item.normalized_label,
            barcode_used=None,
            knowledge_lookup_hit=False,
            consensus_state="VERIFIED",
            candidates_considered=1,
        )
        match = ItemMatch(
            parsed_item_id=item.id,
            status="matched",
            product_ean=fuzzy["ean"],
            match_method="consensus_match",
            match_confidence=float(fuzzy.get("similarity", 1.0)),
            rejected_reason=None,
            top_candidates=(candidate,),
            decision_inputs=decision,
        )
        _emit_item_event(audit_logger, log_level, item, match, "matched_consensus_fuzzy")
        return match

    # 4. STOP — no consensus VERIFIED for this label. Surface the
    # current consensus state when present (PENDING / CONTROVERSE /
    # UNVERIFIED) so admin queues can quantify the split.
    consensus_state_str = consensus.get("state") if consensus is not None else None
    decision = DecisionInputs(
        normalized_label=item.normalized_label,
        barcode_used=None,
        knowledge_lookup_hit=False,
        consensus_state=consensus_state_str,
        candidates_considered=0,
    )
    rejected_reason = f"consensus_state_{consensus_state_str.lower()}" if consensus_state_str else "no_consensus"
    match = ItemMatch(
        parsed_item_id=item.id,
        status="unresolved",
        product_ean=None,
        match_method=None,
        match_confidence=None,
        rejected_reason=rejected_reason,
        top_candidates=(),
        decision_inputs=decision,
    )
    _emit_item_event(audit_logger, log_level, item, match, "unresolved_no_consensus")
    return match


def _emit_item_event(
    audit_logger: AuditLogger,
    log_level: str,
    item: ParsedItem,
    match: ItemMatch,
    event: str,
) -> None:
    """Emit a per-item audit event at level ``verbose`` only.

    ``normal`` and ``production`` levels rely on the aggregated
    ``match_started`` / ``match_completed`` events to keep the audit
    log compact.
    """
    if log_level != "verbose":
        return
    audit_logger(
        phase="match",
        level="verbose",
        event=event,
        payload={
            "parsed_item_id": str(item.id),
            "match_id": str(match.id),
            "status": match.status,
            "match_method": match.match_method,
            "match_confidence": match.match_confidence,
            "rejected_reason": match.rejected_reason,
            "candidates_considered": match.decision_inputs.candidates_considered,
        },
    )


# ── Helpers privés — store ────────────────────────────────────────────────


def _match_store(
    header: ParsedHeader,
    *,
    footer: ParsedFooter | None = None,
    store_lookup: StoreLookup,
    store_by_code: StoreByCodeLookup = _noop_store_by_code,
    audit_logger: AuditLogger,
    log_level: str,
    threshold: float,
    suggest_floor: float,
) -> tuple[StoreMatchStatus, UUID | None, str | None]:
    """Return ``(store_status, store_match_id, store_rejected_reason)``.

    Resolution priority :

    1. **Barcode store_code** — if ``footer.barcode.store_code`` AND
       ``footer.barcode.retailer_key`` are both present, ``store_by_code``
       is queried first. A hit returns ``('matched', uuid, None)`` with
       confidence 1.0 (canonical). A miss falls through to fuzzy.
    2. **Fuzzy header match** :
       - ``score >= threshold`` → ``('matched', uuid, None)``
       - ``suggest_floor <= score < threshold`` → ``('suggested', None, reason)``
       - ``score < suggest_floor`` or no candidate →
         ``('unresolved', None, reason)``
    """
    # 1. Barcode-priority path : canonical key + format hit + DB hit.
    if footer is not None and footer.barcode is not None and footer.barcode.store_code and footer.barcode.retailer_key:
        hit = store_by_code(footer.barcode.retailer_key, footer.barcode.store_code)
        if hit is not None:
            store_id = hit["id"]
            if not isinstance(store_id, UUID):
                raise MatchError(
                    f"store_by_code returned id of type {type(store_id).__name__} ; "
                    "expected UUID per StoreByCodeLookup Protocol."
                )
            if log_level == "verbose":
                audit_logger(
                    phase="match",
                    level="verbose",
                    event="store_matched_via_barcode",
                    payload={
                        "store_id": str(store_id),
                        "retailer_key": footer.barcode.retailer_key,
                        "store_code": footer.barcode.store_code,
                    },
                )
            return "matched", store_id, None
        # Miss : fall through to fuzzy. Audit only at verbose.
        if log_level == "verbose":
            audit_logger(
                phase="match",
                level="verbose",
                event="store_by_code_miss",
                payload={
                    "retailer_key": footer.barcode.retailer_key,
                    "store_code": footer.barcode.store_code,
                },
            )

    # 2. Fuzzy header match — existing cascade.
    results = store_lookup(
        brand=header.brand,
        address_line=header.address_line,
        postcode=header.postcode,
        city=header.city,
        top_n=3,
    )
    if not results:
        if log_level == "verbose":
            audit_logger(
                phase="match",
                level="verbose",
                event="store_unresolved_no_candidate",
                payload={"brand": header.brand, "postcode": header.postcode},
            )
        return "unresolved", None, "no_store_candidate"

    best = results[0]
    best_score = float(best["score"])
    if best_score >= threshold:
        store_id = best["id"]
        if not isinstance(store_id, UUID):
            # Defensive : the Protocol contract says UUID, but a stub
            # might return a stringified value. Fail loud rather than
            # let Pydantic produce a confusing error 4 layers down.
            raise MatchError(
                f"store_lookup returned id of type {type(store_id).__name__} ; expected UUID per StoreLookup Protocol."
            )
        if log_level == "verbose":
            audit_logger(
                phase="match",
                level="verbose",
                event="store_matched",
                payload={"store_id": str(store_id), "score": best_score},
            )
        return "matched", store_id, None

    if best_score >= suggest_floor:
        reason = f"store_low_confidence_{best_score:.3f}"
        if log_level == "verbose":
            audit_logger(
                phase="match",
                level="verbose",
                event="store_suggested",
                payload={"score": best_score, "candidate_count": len(results)},
            )
        return "suggested", None, reason

    reason = f"store_below_threshold_{best_score:.3f}"
    if log_level == "verbose":
        audit_logger(
            phase="match",
            level="verbose",
            event="store_unresolved_below_threshold",
            payload={"score": best_score},
        )
    return "unresolved", None, reason


# ── Errors ────────────────────────────────────────────────────────────────


class MatchError(Exception):
    """Raised on unrecoverable match failures.

    Lookup callbacks that raise (DB connection lost, bug in the wiring)
    propagate naturally ; this exception is reserved for contract
    violations detected here (e.g. a :class:`StoreLookup` that returns
    a non-UUID id). Per ARCH § Anti-patterns we never swallow such
    errors.
    """


__all__ = [
    "DEFAULT_STORE_MATCH_THRESHOLD",
    "DEFAULT_STORE_SUGGEST_FLOOR",
    "DEFAULT_TOP_N_CANDIDATES",
    "ConsensusExactLookup",
    "ConsensusFuzzyLookup",
    "MatchError",
    "ProductByEanLookup",
    "ProductByKnowledgeLookup",
    "RetailerResolver",
    "StoreByCodeLookup",
    "StoreLookup",
    "match_ticket",
]
