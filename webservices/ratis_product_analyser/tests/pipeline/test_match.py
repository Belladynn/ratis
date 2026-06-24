"""Pipeline v3 — Phase 3 matcher (consensus-only refonte 2026-05-02).

NRC Bloc C (cross-retailer) refonte 2026-05-02 :
the consensus cascade is now retailer-keyed (``retailer_id``) instead of
store-keyed (``store_id``). Lookups consume the canonical retailer-keyed
repository signatures (cf. ``ARCH_cross_retailer_consensus.md``
§ "Cascade matcher").

Coverage :

- cascade barcode → knowledge curated → consensus exact → consensus fuzzy → STOP
- store match (fuzzy header + barcode short-circuit)
- traceability : :class:`DecisionInputs` populated for every item
- audit events surface at the right log levels
- defensive contracts (UUID validation, threshold ordering)
- **Bloc C** : retailer-keyed cascade (cross-store same-retailer match,
  retailer_id=None short-circuit, source_type isolation, retailer
  isolation between chains).

Pure functional ; uses lookup stubs to model DB callbacks. The
DB-backed wiring is exercised in :mod:`tests.pipeline.test_orchestrator`.

Tests removed / reshaped in the consensus-only refonte (per R33 we
kept the file and reshaped the contract — the legacy fuzzy_strict
band tests are gone with the dropped path) :

- ``test_fuzzy_match_above_auto_accept`` and friends — replaced by
  ``test_consensus_match_via_exact_lookup`` /
  ``test_consensus_match_via_fuzzy_lookup``.
- ``test_validates_threshold_ordering`` (fuzzy_threshold vs
  fuzzy_auto_accept) — the threshold is gone ; only the store-side
  ordering remains.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from worker.pipeline import match as match_module
from worker.pipeline.match import (
    DEFAULT_STORE_MATCH_THRESHOLD,
    DEFAULT_STORE_SUGGEST_FLOOR,
    MatchError,
    match_ticket,
)
from worker.pipeline.types import (
    Candidate,
    DecisionInputs,
    ItemMatch,
    MatchedTicket,
    ParsedFooter,
    ParsedHeader,
    ParsedItem,
    ParsedReceiptBarcode,
    ParsedTicket,
)

CAPTURED_AT = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


# ── helpers ───────────────────────────────────────────────────────────────


def _make_item(
    *,
    raw_label: str = "HIPRO VANILLE",
    normalized_label: str | None = None,
    barcode: str | None = None,
    quantity: int = 1,
    total_cents: int = 100,
) -> ParsedItem:
    return ParsedItem(
        id=uuid4(),
        raw_label=raw_label,
        normalized_label=normalized_label or raw_label,
        unit_price_cents=total_cents,
        total_cents=total_cents,
        quantity=quantity,
        barcode=barcode,
        source_block_ids=(),
        parsing_issues=(),
    )


def _make_ticket(items: list[ParsedItem], *, with_barcode: bool = False) -> ParsedTicket:
    receipt_id = uuid4()
    barcode = (
        ParsedReceiptBarcode(
            raw="ABCDEF",
            retailer_key="carrefour",
            store_code="0123",
            date=None,
            time=None,
        )
        if with_barcode
        else None
    )
    return ParsedTicket(
        id=uuid4(),
        receipt_id=receipt_id,
        raw_ticket_image_hash="0" * 64,
        header=ParsedHeader(
            brand="Test",
            address_line=None,
            postcode=None,
            city=None,
            phone=None,
            source_block_ids=(),
        ),
        items=tuple(items),
        footer=ParsedFooter(
            total_cents=sum(it.total_cents for it in items),
            vat_breakdown=(),
            payment_method=None,
            barcode=barcode,
            source_block_ids=(),
        ),
        purchased_at=CAPTURED_AT,
    ).with_jsonb_hash()


_STORE_ID = UUID("11111111-1111-1111-1111-111111111111")
_RETAILER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _stub_store_lookup(score: float = 0.95):
    def lookup(*, brand=None, address_line=None, postcode=None, city=None, top_n=3):
        return [
            {
                "id": _STORE_ID,
                "name": "Stub",
                "address": "addr",
                "score": score,
            }
        ]

    return lookup


def _stub_retailer_resolver(retailer_id: UUID | None = _RETAILER_ID):
    """Default retailer resolver : returns ``_RETAILER_ID`` for any store."""

    def resolve(store_id: UUID) -> UUID | None:
        return retailer_id

    return resolve


# ── 1. Item cascade — barcode ─────────────────────────────────────────────


def test_barcode_match_when_ean_known() -> None:
    item = _make_item(barcode="3017620422003", normalized_label="NUTELLA 400G")
    ticket = _make_ticket([item])

    def by_ean(ean):
        return {"ean": "3017620422003", "label": "Nutella 400g"}

    out = match_ticket(
        ticket,
        product_by_ean=by_ean,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    assert len(out.item_matches) == 1
    m = out.item_matches[0]
    assert m.status == "matched"
    assert m.match_method == "barcode"
    assert m.match_confidence == 1.0
    assert m.product_ean == "3017620422003"
    assert m.rejected_reason is None
    assert m.decision_inputs.barcode_used == "3017620422003"


def test_barcode_unresolved_when_ean_unknown_no_consensus_fallback() -> None:
    """A barcode set but unknown in DB is anomalous — the cascade does
    NOT silently fall back to consensus on the cleaned label."""
    item = _make_item(barcode="9999999999999", normalized_label="MYSTERY")
    ticket = _make_ticket([item])
    consensus_called: list = []

    def by_ean(ean):
        return None

    def consensus_exact(retailer_id, label):
        consensus_called.append((retailer_id, label))
        return {"ean": "X", "state": "VERIFIED"}

    out = match_ticket(
        ticket,
        product_by_ean=by_ean,
        consensus_exact=consensus_exact,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.rejected_reason == "barcode_unknown_in_db"
    assert m.product_ean is None
    assert m.match_method is None
    assert consensus_called == []  # never queried — no fallback by design


# ── 2. Item cascade — knowledge curated ───────────────────────────────────


def test_knowledge_match_when_no_barcode() -> None:
    item = _make_item(normalized_label="HIPRO BRE SAV FRSE")
    ticket = _make_ticket([item])

    def knowledge(label):
        return {"ean": "EAN_K", "label": "Hipro Fraise", "score": 0.97}

    out = match_ticket(
        ticket,
        product_by_knowledge=knowledge,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "matched"
    assert m.match_method == "knowledge"
    assert m.match_confidence == 0.97
    assert m.product_ean == "EAN_K"
    assert m.decision_inputs.knowledge_lookup_hit is True


def test_knowledge_default_score_when_missing() -> None:
    item = _make_item()
    ticket = _make_ticket([item])

    def knowledge(label):
        return {"ean": "E", "label": "L"}  # no score

    out = match_ticket(
        ticket,
        product_by_knowledge=knowledge,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    assert out.item_matches[0].match_confidence == 0.95


# ── 3. Item cascade — consensus exact ─────────────────────────────────────


def test_consensus_match_via_exact_lookup() -> None:
    item = _make_item(normalized_label="HIPRO BRE SAV FRSE")
    ticket = _make_ticket([item])

    def consensus(retailer_id, label):
        assert retailer_id == _RETAILER_ID
        return {"ean": "EAN_C", "state": "VERIFIED"}

    out = match_ticket(
        ticket,
        consensus_exact=consensus,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "matched"
    assert m.match_method == "consensus_match"
    assert m.product_ean == "EAN_C"
    assert m.match_confidence == 1.0
    assert m.decision_inputs.consensus_state == "VERIFIED"


def test_consensus_pending_returns_unresolved_with_state_recorded() -> None:
    item = _make_item(normalized_label="HIPRO")
    ticket = _make_ticket([item])

    def consensus(retailer_id, label):
        return {"ean": "EAN_C", "state": "PENDING"}

    out = match_ticket(
        ticket,
        consensus_exact=consensus,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.match_method is None
    assert m.product_ean is None
    assert m.rejected_reason == "consensus_state_pending"
    assert m.decision_inputs.consensus_state == "PENDING"


def test_consensus_controverse_returns_unresolved() -> None:
    item = _make_item(normalized_label="X")
    ticket = _make_ticket([item])

    def consensus(retailer_id, label):
        return {"ean": "E", "state": "CONTROVERSE"}

    out = match_ticket(
        ticket,
        consensus_exact=consensus,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.rejected_reason == "consensus_state_controverse"


# ── 4. Item cascade — consensus fuzzy fallback ────────────────────────────


def test_consensus_match_via_fuzzy_lookup() -> None:
    """When exact lookup misses, the fuzzy fallback can still resolve
    OCR variants against a VERIFIED neighbour."""
    item = _make_item(normalized_label="HIPROA BRE SAV FRSE")
    ticket = _make_ticket([item])

    def consensus_exact(retailer_id, label):
        return None

    def consensus_fuzzy(retailer_id, label):
        assert retailer_id == _RETAILER_ID
        return {
            "ean": "EAN_F",
            "label": "HIPRO BRE SAV FRSE",
            "similarity": 0.857,
        }

    out = match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        consensus_fuzzy=consensus_fuzzy,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "matched"
    assert m.match_method == "consensus_match"
    assert m.product_ean == "EAN_F"
    assert m.match_confidence == pytest.approx(0.857)


def test_no_consensus_anywhere_returns_unresolved() -> None:
    item = _make_item(normalized_label="UNKNOWN LABEL")
    ticket = _make_ticket([item])

    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.rejected_reason == "no_consensus"
    assert m.match_method is None
    assert m.product_ean is None


def test_no_store_short_circuits_consensus() -> None:
    """Without a matched store, consensus lookups can't fire (the ledger
    is keyed on retailer_id resolved from store_id) — the cascade
    short-circuits with a dedicated rejected_reason."""
    item = _make_item(normalized_label="SOMETHING")
    ticket = _make_ticket([item])
    consensus_exact_called: list = []

    def consensus_exact(retailer_id, label):
        consensus_exact_called.append((retailer_id, label))
        return None

    # store_lookup returns no candidate → store_status='unresolved'
    out = match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        store_lookup=lambda **kw: [],
        retailer_resolver=_stub_retailer_resolver(),
    )

    assert out.store_status == "unresolved"
    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.rejected_reason == "no_store_for_consensus"
    assert consensus_exact_called == []  # never invoked


# ── 5. Decision-inputs traceability ───────────────────────────────────────


def test_decision_inputs_records_barcode_path() -> None:
    item = _make_item(barcode="EAN", normalized_label="LBL")
    ticket = _make_ticket([item])

    def by_ean(ean):
        return {"ean": "EAN", "label": "Label"}

    out = match_ticket(
        ticket,
        product_by_ean=by_ean,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )
    d = out.item_matches[0].decision_inputs
    assert d.barcode_used == "EAN"
    assert d.knowledge_lookup_hit is False
    assert d.consensus_state is None
    assert d.candidates_considered == 1


def test_decision_inputs_records_consensus_path() -> None:
    item = _make_item(normalized_label="LBL")
    ticket = _make_ticket([item])

    def consensus(r, l):  # noqa: E741
        return {"ean": "E", "state": "VERIFIED"}

    out = match_ticket(
        ticket,
        consensus_exact=consensus,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )
    d = out.item_matches[0].decision_inputs
    assert d.barcode_used is None
    assert d.knowledge_lookup_hit is False
    assert d.consensus_state == "VERIFIED"


# ── 6. Store match ─────────────────────────────────────────────────────────


def test_store_matched_when_score_high() -> None:
    ticket = _make_ticket([_make_item()])
    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(score=0.95),
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "matched"
    assert out.store_match_id == _STORE_ID


def test_store_suggested_when_score_mid() -> None:
    ticket = _make_ticket([_make_item()])
    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(score=0.65),
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "suggested"
    assert out.store_match_id is None


def test_store_unresolved_when_score_low() -> None:
    ticket = _make_ticket([_make_item()])
    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(score=0.30),
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "unresolved"


def test_store_unresolved_when_no_candidates() -> None:
    ticket = _make_ticket([_make_item()])
    out = match_ticket(
        ticket,
        store_lookup=lambda **kw: [],
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "unresolved"
    assert out.store_rejected_reason == "no_store_candidate"


def test_store_match_error_when_id_not_uuid() -> None:
    ticket = _make_ticket([_make_item()])

    def bad(**kw):
        return [{"id": "not-a-uuid", "name": "X", "address": "Y", "score": 0.99}]

    with pytest.raises(MatchError):
        match_ticket(ticket, store_lookup=bad, retailer_resolver=_stub_retailer_resolver())


def test_store_matched_via_barcode_when_store_code_hits() -> None:
    ticket = _make_ticket([_make_item()], with_barcode=True)

    def by_code(r, c):
        return {"id": _STORE_ID, "name": "X", "address": "Y", "score": 1.0}

    out = match_ticket(
        ticket,
        store_by_code=by_code,
        store_lookup=lambda **kw: [],
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "matched"
    assert out.store_match_id == _STORE_ID


def test_store_falls_back_to_fuzzy_when_store_by_code_misses() -> None:
    ticket = _make_ticket([_make_item()], with_barcode=True)

    def by_code(r, c):
        return None

    out = match_ticket(
        ticket,
        store_by_code=by_code,
        store_lookup=_stub_store_lookup(score=0.95),
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "matched"
    assert out.store_match_id == _STORE_ID


# ── 7. Audit events ───────────────────────────────────────────────────────


def test_match_started_event_emitted() -> None:
    events: list[dict] = []
    ticket = _make_ticket([_make_item()])
    match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
        audit_logger=lambda **kw: events.append(kw),
    )
    assert any(e["event"] == "match_started" for e in events)


def test_match_completed_event_with_counts() -> None:
    events: list[dict] = []
    ticket = _make_ticket([_make_item(), _make_item()])
    match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
        audit_logger=lambda **kw: events.append(kw),
    )
    completed = next(e for e in events if e["event"] == "match_completed")
    # 2 items, no consensus → both unresolved
    assert completed["payload"]["unresolved_count"] == 2


def test_audit_per_item_only_on_verbose() -> None:
    events_normal: list[dict] = []
    events_verbose: list[dict] = []
    ticket = _make_ticket([_make_item()])

    match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
        log_level="normal",
        audit_logger=lambda **kw: events_normal.append(kw),
    )
    match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
        log_level="verbose",
        audit_logger=lambda **kw: events_verbose.append(kw),
    )

    # ``unresolved_no_consensus`` is per-item, only emitted on verbose
    assert not any(e["event"].startswith("unresolved_") for e in events_normal)
    assert any(e["event"].startswith("unresolved_") for e in events_verbose)


# ── 8. Invariants / property-style ────────────────────────────────────────


def test_every_item_produces_exactly_one_itemmatch() -> None:
    items = [_make_item() for _ in range(4)]
    ticket = _make_ticket(items)
    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert len(out.item_matches) == len(items)
    assert {m.parsed_item_id for m in out.item_matches} == {it.id for it in items}


def test_unresolved_always_has_rejected_reason() -> None:
    items = [
        _make_item(barcode="999", normalized_label="A"),  # barcode_unknown_in_db
        _make_item(normalized_label="B"),  # no_consensus
    ]
    ticket = _make_ticket(items)
    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )
    for m in out.item_matches:
        if m.status != "matched":
            assert m.rejected_reason is not None


def test_empty_parsed_ticket_yields_empty_item_matches() -> None:
    ticket = _make_ticket([])
    out = match_ticket(
        ticket,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.item_matches == ()


def test_match_idempotent_under_stable_stubs() -> None:
    item = _make_item(normalized_label="L")
    ticket = _make_ticket([item])

    def consensus(r, l):  # noqa: E741
        return {"ean": "E", "state": "VERIFIED"}

    a = match_ticket(
        ticket,
        consensus_exact=consensus,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )
    b = match_ticket(
        ticket,
        consensus_exact=consensus,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    assert a.item_matches[0].status == b.item_matches[0].status
    assert a.item_matches[0].product_ean == b.item_matches[0].product_ean
    assert a.item_matches[0].match_method == b.item_matches[0].match_method


# ── 9. Defensive contracts ────────────────────────────────────────────────


def test_validates_store_threshold_ordering() -> None:
    ticket = _make_ticket([_make_item()])
    with pytest.raises(ValueError, match="store_suggest_floor"):
        match_ticket(
            ticket,
            store_suggest_floor=0.95,
            store_threshold=0.50,
        )


def test_default_thresholds_documented_module_constants() -> None:
    assert DEFAULT_STORE_MATCH_THRESHOLD == 0.80
    assert DEFAULT_STORE_SUGGEST_FLOOR == 0.50


# ── 10. End-to-end small smoke ────────────────────────────────────────────


def test_full_ticket_three_items_three_paths() -> None:
    """One item per cascade branch : barcode, knowledge, consensus."""
    barcode_item = _make_item(barcode="EAN_B", normalized_label="B")
    knowledge_item = _make_item(normalized_label="K")
    consensus_item = _make_item(normalized_label="C")

    ticket = _make_ticket([barcode_item, knowledge_item, consensus_item])

    def by_ean(ean):
        return {"ean": ean, "label": "Barcode-product"}

    def knowledge(label):
        return {"ean": "EAN_K", "label": "K-product"} if label == "K" else None

    def consensus_exact(r, l):  # noqa: E741
        return {"ean": "EAN_C", "state": "VERIFIED"} if l == "C" else None

    out = match_ticket(
        ticket,
        product_by_ean=by_ean,
        product_by_knowledge=knowledge,
        consensus_exact=consensus_exact,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    methods = [m.match_method for m in out.item_matches]
    assert methods == ["barcode", "knowledge", "consensus_match"]


# ── 11. Pre-computed store_id (orchestrator override) ─────────────────────


def test_store_match_id_override_skips_store_lookup() -> None:
    """The orchestrator can pre-compute store match. When ``store_match_id``
    is passed, the matcher trusts it and short-circuits the store
    cascade — useful for replay paths that already know the store."""
    ticket = _make_ticket([_make_item()])
    store_lookup_called: list = []

    out = match_ticket(
        ticket,
        store_match_id=_STORE_ID,
        store_lookup=lambda **kw: store_lookup_called.append(1) or [],
        retailer_resolver=_stub_retailer_resolver(),
    )
    assert out.store_status == "matched"
    assert out.store_match_id == _STORE_ID
    assert store_lookup_called == []


# ── 12. Public API surface (smoke-import) ─────────────────────────────────


def test_public_module_exposes_consensus_protocols() -> None:
    """The module's public surface advertises the new lookup protocols
    (and no longer exposes ``ProductFuzzySearch`` / fuzzy thresholds).

    Bloc C : the cascade now also exposes :class:`RetailerResolver` so
    callers can wire :func:`repositories.retailer_resolution.resolve_retailer_id`
    directly.
    """
    assert "ConsensusExactLookup" in match_module.__all__
    assert "ConsensusFuzzyLookup" in match_module.__all__
    assert "RetailerResolver" in match_module.__all__
    assert "ProductFuzzySearch" not in match_module.__all__
    assert "DEFAULT_FUZZY_THRESHOLD" not in match_module.__all__
    assert "DEFAULT_FUZZY_AUTO_ACCEPT" not in match_module.__all__


# Reference imports kept for IDE / future tests — not assertions.
_ALL_TYPES = (Candidate, DecisionInputs, ItemMatch, MatchedTicket)


# ── 13. Bloc C — retailer-keyed cascade (cross-retailer consensus) ────────


def test_consensus_exact_matches_cross_store_same_retailer() -> None:
    """A user scanning at Intermarché Lyon resolves to retailer_id=
    ``intermarche``. A consensus row exists for that retailer with the
    same normalized_label (originally written from Intermarché Paris).
    The matcher must hit Stage 3 → ``consensus_match`` even though the
    scan store is different from the stores that wrote the ledger
    rows.
    """
    other_store = UUID("22222222-2222-2222-2222-222222222222")
    intermarche = UUID("aaaaaaaa-1111-1111-1111-111111111111")

    item = _make_item(normalized_label="HIPRO ABRC")
    ticket = _make_ticket([item])

    consensus_calls: list = []

    def store_lookup(**kw):
        # Returns a different store id from the ones that wrote the
        # ledger — but ``retailer_resolver`` will still map to the same
        # retailer_id for both.
        return [{"id": other_store, "name": "Inter Lyon", "address": "x", "score": 0.99}]

    def retailer_resolver(store_id):
        # Both stores belong to the same retailer chain.
        return intermarche

    def consensus_exact(retailer_id, label):
        consensus_calls.append((retailer_id, label))
        return {"ean": "EAN_HIPRO_ABRC", "state": "VERIFIED"}

    out = match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        store_lookup=store_lookup,
        retailer_resolver=retailer_resolver,
    )

    assert consensus_calls == [(intermarche, "HIPRO ABRC")]
    m = out.item_matches[0]
    assert m.status == "matched"
    assert m.match_method == "consensus_match"
    assert m.product_ean == "EAN_HIPRO_ABRC"


def test_consensus_fuzzy_matches_close_label() -> None:
    """Stage 4 : when Stage 3 misses, the fuzzy retailer-wide fallback
    is keyed on retailer_id, not store_id."""
    intermarche = UUID("aaaaaaaa-2222-2222-2222-222222222222")
    item = _make_item(normalized_label="HIPRO ABRC BRE")
    ticket = _make_ticket([item])

    fuzzy_calls: list = []

    def consensus_exact(retailer_id, label):
        return None

    def consensus_fuzzy(retailer_id, label):
        fuzzy_calls.append((retailer_id, label))
        return {
            "ean": "EAN_HIPRO_ABRICOT",
            "label": "HIPRO ABRICOT BREBIS",
            "similarity": 0.83,
        }

    out = match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        consensus_fuzzy=consensus_fuzzy,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(retailer_id=intermarche),
    )

    assert fuzzy_calls == [(intermarche, "HIPRO ABRC BRE")]
    m = out.item_matches[0]
    assert m.status == "matched"
    assert m.product_ean == "EAN_HIPRO_ABRICOT"
    assert m.match_confidence == pytest.approx(0.83)


def test_consensus_skipped_when_retailer_id_null() -> None:
    """A user-suggested store with ``retailer_id IS NULL`` must NOT hit
    the consensus stages : the cascade short-circuits with a dedicated
    rejected_reason ``no_retailer_for_consensus`` and never invokes the
    consensus callbacks (they would explode on a NULL retailer_id).
    """
    item = _make_item(normalized_label="WHATEVER")
    ticket = _make_ticket([item])

    consensus_exact_called: list = []
    consensus_fuzzy_called: list = []

    def consensus_exact(retailer_id, label):
        consensus_exact_called.append((retailer_id, label))
        return {"ean": "X", "state": "VERIFIED"}

    def consensus_fuzzy(retailer_id, label):
        consensus_fuzzy_called.append((retailer_id, label))
        return {"ean": "Y", "label": "Z", "similarity": 1.0}

    # Store match resolves, but the retailer_resolver returns None
    # (user-suggested store pending admin validation).
    out = match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        consensus_fuzzy=consensus_fuzzy,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(retailer_id=None),
    )

    # Store still matched (the store exists, just not yet retailer-attached).
    assert out.store_status == "matched"
    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.match_method is None
    assert m.product_ean is None
    assert m.rejected_reason == "no_retailer_for_consensus"
    assert consensus_exact_called == []
    assert consensus_fuzzy_called == []


def test_consensus_filters_other_retailer() -> None:
    """A consensus_exact callback that returns ``None`` for the queried
    retailer (because the only ledger rows are from a different chain)
    must surface as ``unresolved`` — the matcher does NOT fall back to
    other retailers."""
    intermarche = UUID("aaaaaaaa-3333-3333-3333-333333333333")
    carrefour = UUID("bbbbbbbb-3333-3333-3333-333333333333")

    item = _make_item(normalized_label="HIPRO ABRC")
    ticket = _make_ticket([item])

    queried_retailers: list = []

    def consensus_exact(retailer_id, label):
        queried_retailers.append(retailer_id)
        # Ledger only has Carrefour rows for this label — mismatch with
        # Intermarché query → None.
        if retailer_id == carrefour:
            return {"ean": "WRONG", "state": "VERIFIED"}
        return None

    out = match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(retailer_id=intermarche),
    )

    assert queried_retailers == [intermarche]
    m = out.item_matches[0]
    assert m.status == "unresolved"
    assert m.match_method is None


def test_consensus_filters_other_source_type() -> None:
    """The matcher cascade for a receipt scan only queries
    ``source_type='receipt'`` rows. ESL ledger rows are NOT consulted
    by the receipt cascade — they live in a separate stage (V2) and
    are isolated by the repository signature.

    This is encoded at the wiring layer (orchestrator wires the
    callback to ``source_type='receipt'``). The matcher itself stays
    agnostic ; this test pins the contract that the callback receives
    only ``(retailer_id, label)`` and source_type is hidden from the
    cascade.
    """
    item = _make_item(normalized_label="HIPRO ABRC")
    ticket = _make_ticket([item])

    received_args: list = []

    def consensus_exact(retailer_id, label):
        received_args.append((retailer_id, label))
        return None

    match_ticket(
        ticket,
        consensus_exact=consensus_exact,
        store_lookup=_stub_store_lookup(),
        retailer_resolver=_stub_retailer_resolver(),
    )

    # Only 2 positional args : (retailer_id, label). source_type is
    # NOT part of the cascade contract — the wiring layer pins it.
    assert len(received_args) == 1
    assert len(received_args[0]) == 2
