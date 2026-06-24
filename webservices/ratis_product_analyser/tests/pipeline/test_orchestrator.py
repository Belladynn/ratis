"""Orchestrator tests — composes the 4 phases via mocks (no real OCR / LLM).

Strategy : monkeypatch the pure-functional phase entry points
(``extract.extract_raw_ticket`` / ``comprehend.comprehend_ticket`` /
``match.match_ticket`` / ``persist.persist_pipeline_result``) so we
verify the orchestration layer (call order, kwarg wiring, audit, error
propagation) without paying any DB or OCR cost. A separate
``@pytest.mark.integration`` smoke runs the real DB-backed lookups
end-to-end against a stub LLM client.

Cf. ``ARCH_receipt_pipeline.md`` § Plan de migration / § Anti-patterns.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from worker.pipeline import orchestrator
from worker.pipeline.llm_clients import StubLLMClient
from worker.pipeline.types import (
    DecisionInputs,
    ItemMatch,
    MatchedTicket,
    ParsedFooter,
    ParsedHeader,
    ParsedItem,
    ParsedTicket,
    RawBlock,
    RawTicket,
    compute_block_hash,
)

# Anchored to "now - 1 day" so the PR4 anti-fraud age check
# (``consensus.ticket_max_age_days = 7``) doesn't trigger.
CAPTURED_AT = datetime.now(UTC) - timedelta(days=1)


# ── Builders ──────────────────────────────────────────────────────────────


def _make_raw_ticket(receipt_id: uuid.UUID | None = None) -> RawTicket:
    return RawTicket(
        receipt_id=receipt_id or uuid.uuid4(),
        blocks=(),
        barcodes=(),
        image_hash="b" * 64,
        ocr_engine_version="paddleocr-test-fr",
        captured_at=CAPTURED_AT,
    )


def _make_parsed_ticket(
    *,
    receipt_id: uuid.UUID,
    items: tuple[ParsedItem, ...] = (),
) -> ParsedTicket:
    header = ParsedHeader(
        brand="INTERMARCHE",
        postcode="92400",
        city="COURBEVOIE",
        source_block_ids=(),
    )
    footer = ParsedFooter(
        total_cents=999,
        vat_breakdown=(),
        item_count_declared=len(items),
        source_block_ids=(),
    )
    return ParsedTicket(
        receipt_id=receipt_id,
        items=items,
        header=header,
        footer=footer,
        purchased_at=CAPTURED_AT,
        raw_ticket_image_hash="b" * 64,
    ).with_jsonb_hash()


def _make_item(label: str = "BANANE") -> ParsedItem:
    return ParsedItem(
        raw_label=label,
        normalized_label=label.upper(),
        quantity=1,
        unit_price_cents=100,
        total_cents=100,
        barcode=None,
        source_block_ids=(),
        parsing_issues=(),
    )


def _make_matched(parsed: ParsedTicket) -> MatchedTicket:
    matches = tuple(
        ItemMatch(
            parsed_item_id=it.id,
            status="unresolved",
            rejected_reason="no_consensus",
            decision_inputs=DecisionInputs(
                normalized_label=it.normalized_label,
                barcode_used=None,
                knowledge_lookup_hit=False,
                consensus_state=None,
                candidates_considered=0,
            ),
        )
        for it in parsed.items
    )
    return MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=matches,
        store_match_id=None,
        store_status="unresolved",
        store_rejected_reason="no_store_candidate",
    )


# ── Tests : phase composition order via patching ─────────────────────────


def test_run_pipeline_calls_4_phases_in_order(monkeypatch, db):
    calls: list[str] = []

    raw = _make_raw_ticket()
    parsed = _make_parsed_ticket(receipt_id=raw.receipt_id, items=(_make_item(),))
    matched = _make_matched(parsed)
    persist_result = {
        "parsed_ticket_id": parsed.id,
        "receipt_id": raw.receipt_id,
        "scan_ids": [uuid.uuid4()],
        "store_candidate_id": None,
        "audit_event_count": 1,
    }

    def fake_extract(image_bytes, **kwargs):
        calls.append("extract")
        return raw

    def fake_comprehend(raw_in, **kwargs):
        calls.append("comprehend")
        return parsed

    def fake_match(parsed_in, **kwargs):
        calls.append("match")
        return matched

    def fake_persist(**kwargs):
        calls.append("persist")
        return persist_result

    monkeypatch.setattr(orchestrator.extract, "extract_raw_ticket", fake_extract)
    monkeypatch.setattr(orchestrator.comprehend, "comprehend_ticket", fake_comprehend)
    monkeypatch.setattr(orchestrator.match, "match_ticket", fake_match)
    monkeypatch.setattr(orchestrator.persist, "persist_pipeline_result", fake_persist)

    result = orchestrator.run_pipeline(
        b"image",
        db=db,
        user_id=uuid.uuid4(),
        captured_at=CAPTURED_AT,
        llm_client=StubLLMClient({"items": []}),
    )

    assert calls == ["extract", "comprehend", "match", "persist"]
    assert result == persist_result


def test_run_pipeline_uses_provided_llm_client(monkeypatch, db):
    """Injected StubLLMClient must be forwarded to comprehend (no Anthropic)."""
    seen_clients: list[Any] = []

    raw = _make_raw_ticket()
    parsed = _make_parsed_ticket(receipt_id=raw.receipt_id, items=(_make_item(),))
    matched = _make_matched(parsed)

    monkeypatch.setattr(
        orchestrator.extract,
        "extract_raw_ticket",
        lambda image_bytes, **kwargs: raw,
    )

    def fake_comprehend(raw_in, *, llm_client, **kwargs):
        seen_clients.append(llm_client)
        return parsed

    monkeypatch.setattr(orchestrator.comprehend, "comprehend_ticket", fake_comprehend)
    monkeypatch.setattr(
        orchestrator.match,
        "match_ticket",
        lambda parsed_in, **kwargs: matched,
    )
    monkeypatch.setattr(
        orchestrator.persist,
        "persist_pipeline_result",
        lambda **kwargs: {
            "parsed_ticket_id": parsed.id,
            "receipt_id": raw.receipt_id,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        },
    )

    stub = StubLLMClient({"items": []})
    orchestrator.run_pipeline(
        b"image",
        db=db,
        captured_at=CAPTURED_AT,
        llm_client=stub,
    )

    assert seen_clients == [stub]


def test_run_pipeline_default_llm_raises_without_api_key(monkeypatch, db):
    """When llm_client is None and LLM_API_KEY is not set, the AnthropicLLMClient
    constructor raises ValueError — the orchestrator never silently degrades."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    # Patch extract so we never even reach OCR.
    monkeypatch.setattr(
        orchestrator.extract,
        "extract_raw_ticket",
        lambda image_bytes, **kwargs: _make_raw_ticket(),
    )

    with pytest.raises(ValueError, match="API key"):
        orchestrator.run_pipeline(b"image", db=db, captured_at=CAPTURED_AT)


def test_run_pipeline_debug_forces_verbose(monkeypatch, db):
    seen: dict[str, Any] = {}

    raw = _make_raw_ticket()
    parsed = _make_parsed_ticket(receipt_id=raw.receipt_id, items=(_make_item(),))
    matched = _make_matched(parsed)

    def fake_extract(image_bytes, *, log_level, **kwargs):
        seen["extract"] = log_level
        return raw

    def fake_comprehend(raw_in, *, log_level, **kwargs):
        seen["comprehend"] = log_level
        return parsed

    def fake_match(parsed_in, *, log_level, **kwargs):
        seen["match"] = log_level
        return matched

    def fake_persist(*, log_level, **kwargs):
        seen["persist"] = log_level
        return {
            "parsed_ticket_id": parsed.id,
            "receipt_id": raw.receipt_id,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        }

    monkeypatch.setattr(orchestrator.extract, "extract_raw_ticket", fake_extract)
    monkeypatch.setattr(orchestrator.comprehend, "comprehend_ticket", fake_comprehend)
    monkeypatch.setattr(orchestrator.match, "match_ticket", fake_match)
    monkeypatch.setattr(orchestrator.persist, "persist_pipeline_result", fake_persist)

    orchestrator.run_pipeline(
        b"image",
        db=db,
        captured_at=CAPTURED_AT,
        llm_client=StubLLMClient({}),
        debug=True,
    )

    assert seen == {
        "extract": "verbose",
        "comprehend": "verbose",
        "match": "verbose",
        "persist": "verbose",
    }


def test_run_pipeline_emits_pipeline_completed_event(monkeypatch, db):
    """The orchestrator writes a final `pipeline_completed` audit event."""
    raw = _make_raw_ticket()
    parsed = _make_parsed_ticket(receipt_id=raw.receipt_id, items=(_make_item(),))
    matched = _make_matched(parsed)
    monkeypatch.setattr(
        orchestrator.extract,
        "extract_raw_ticket",
        lambda image_bytes, **kwargs: raw,
    )
    monkeypatch.setattr(
        orchestrator.comprehend,
        "comprehend_ticket",
        lambda raw_in, **kwargs: parsed,
    )
    monkeypatch.setattr(
        orchestrator.match,
        "match_ticket",
        lambda parsed_in, **kwargs: matched,
    )
    monkeypatch.setattr(
        orchestrator.persist,
        "persist_pipeline_result",
        lambda **kwargs: {
            "parsed_ticket_id": parsed.id,
            "receipt_id": raw.receipt_id,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        },
    )

    orchestrator.run_pipeline(
        b"image",
        db=db,
        captured_at=CAPTURED_AT,
        llm_client=StubLLMClient({}),
    )
    db.commit()

    events = db.execute(text("SELECT phase, event FROM pipeline_audit_log WHERE event = 'pipeline_completed'")).all()
    assert len(events) == 1


# ── parsing_issues pre-Phase-3 audit hook ────────────────────────────────


def test_emit_parsing_issues_emits_audit_event_per_item():
    seen: list[dict] = []

    def audit(*, phase, level, event, payload=None):
        seen.append({"phase": phase, "event": event, "payload": payload})

    item_with_issues = ParsedItem(
        raw_label="HIPRO VANILLE",
        normalized_label="HIPRO VANILLE",
        quantity=1,
        unit_price_cents=100,
        total_cents=100,
        barcode=None,
        source_block_ids=(),
        parsing_issues=("total_unit_qty_mismatch",),
    )
    item_clean = _make_item("BANANE")
    parsed = _make_parsed_ticket(
        receipt_id=uuid.uuid4(),
        items=(item_with_issues, item_clean),
    )

    orchestrator._emit_parsing_issues_events(parsed, audit_logger=audit)

    issues_events = [e for e in seen if e["event"] == "item_has_parsing_issues"]
    assert len(issues_events) == 1
    assert issues_events[0]["payload"]["parsing_issues"] == ["total_unit_qty_mismatch"]


# ── DB-backed lookups (smoke) ────────────────────────────────────────────


def test_orchestrator_audit_logger_filters_by_log_level(db):
    audit = orchestrator._make_db_audit_logger(db, log_level="production")

    audit(phase="match", level="verbose", event="should_drop", payload={})
    audit(phase="match", level="normal", event="should_drop_too", payload={})
    audit(phase="match", level="production", event="should_keep", payload={})
    db.commit()

    rows = db.execute(text("SELECT event FROM pipeline_audit_log WHERE phase = 'match'")).all()
    events = {r.event for r in rows}
    assert "should_keep" in events
    assert "should_drop" not in events
    assert "should_drop_too" not in events


def test_product_by_ean_lookup_hits_db(db):
    from ratis_core.models.product import Product

    p = Product(ean="3000000000017", name="Test Lookup", source="off")
    db.add(p)
    db.flush()
    db.commit()

    lookup = orchestrator._make_product_by_ean(db)
    assert lookup("3000000000017") == {"ean": "3000000000017", "label": "Test Lookup"}
    assert lookup("0000000000000") is None


def test_store_lookup_returns_empty_when_no_brand(db):
    lookup = orchestrator._make_store_lookup(db)
    assert lookup(brand=None, address_line=None, postcode=None, city=None) == []


def test_consensus_exact_returns_none_on_clean_db(db):
    """Exact consensus lookup returns None when the ledger is empty for
    the given ``(retailer_id, label)`` pair (Bloc C refonte 2026-05-02 :
    retailer-keyed canonical signature)."""
    lookup = orchestrator._make_consensus_exact(db)
    assert lookup(uuid.uuid4(), "ZZZ_NONEXISTENT_LABEL_XX9") is None


def test_consensus_fuzzy_returns_none_on_clean_db(db):
    """Fuzzy consensus fallback returns None when no neighbour passes
    the strict gates (Bloc C : retailer-keyed)."""
    lookup = orchestrator._make_consensus_fuzzy(db)
    assert lookup(uuid.uuid4(), "ZZZ_NONEXISTENT_LABEL_XX9") is None


def test_retailer_resolver_returns_none_for_unknown_store(db):
    """Bloc C : the retailer resolver returns ``None`` for a UUID that
    does not exist in ``stores`` (defensive — avoids crashing the
    cascade on stale UUIDs ; the matcher then short-circuits the
    consensus stages)."""
    resolve = orchestrator._make_retailer_resolver(db)
    assert resolve(uuid.uuid4()) is None


# ── Barcode wiring (PR-B) ─────────────────────────────────────────────────


def test_orchestrator_wires_barcode_parser_callback(monkeypatch, db):
    """The orchestrator must inject a barcode_parser kwarg into comprehend_ticket."""
    seen_kwargs: dict[str, Any] = {}

    raw = _make_raw_ticket()
    parsed = _make_parsed_ticket(receipt_id=raw.receipt_id, items=(_make_item(),))
    matched = _make_matched(parsed)

    def fake_comprehend(raw_in, **kwargs):
        seen_kwargs.update(kwargs)
        return parsed

    monkeypatch.setattr(
        orchestrator.extract,
        "extract_raw_ticket",
        lambda image_bytes, **kwargs: raw,
    )
    monkeypatch.setattr(orchestrator.comprehend, "comprehend_ticket", fake_comprehend)
    monkeypatch.setattr(
        orchestrator.match,
        "match_ticket",
        lambda parsed_in, **kwargs: matched,
    )
    monkeypatch.setattr(
        orchestrator.persist,
        "persist_pipeline_result",
        lambda **kwargs: {
            "parsed_ticket_id": parsed.id,
            "receipt_id": raw.receipt_id,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        },
    )

    orchestrator.run_pipeline(
        b"image",
        db=db,
        captured_at=CAPTURED_AT,
        llm_client=StubLLMClient({}),
    )
    assert "barcode_parser" in seen_kwargs
    assert callable(seen_kwargs["barcode_parser"])


def test_orchestrator_wires_store_by_code_callback(monkeypatch, db):
    """The orchestrator must inject a store_by_code kwarg into match_ticket."""
    seen_kwargs: dict[str, Any] = {}

    raw = _make_raw_ticket()
    parsed = _make_parsed_ticket(receipt_id=raw.receipt_id, items=(_make_item(),))
    matched = _make_matched(parsed)

    def fake_match(parsed_in, **kwargs):
        seen_kwargs.update(kwargs)
        return matched

    monkeypatch.setattr(
        orchestrator.extract,
        "extract_raw_ticket",
        lambda image_bytes, **kwargs: raw,
    )
    monkeypatch.setattr(
        orchestrator.comprehend,
        "comprehend_ticket",
        lambda raw_in, **kwargs: parsed,
    )
    monkeypatch.setattr(orchestrator.match, "match_ticket", fake_match)
    monkeypatch.setattr(
        orchestrator.persist,
        "persist_pipeline_result",
        lambda **kwargs: {
            "parsed_ticket_id": parsed.id,
            "receipt_id": raw.receipt_id,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        },
    )

    orchestrator.run_pipeline(
        b"image",
        db=db,
        captured_at=CAPTURED_AT,
        llm_client=StubLLMClient({}),
    )
    assert "store_by_code" in seen_kwargs
    assert callable(seen_kwargs["store_by_code"])


def test_make_store_by_code_returns_none_on_clean_db(db):
    """No stores in DB → lookup returns None."""
    lookup = orchestrator._make_store_by_code(db)
    assert lookup("intermarche", "00100") is None


def test_make_store_by_code_normalizes_retailer(db):
    """Retailer with accents is normalized before lookup (defensive)."""
    lookup = orchestrator._make_store_by_code(db)
    # Defensive : even if a caller passes the brand verbatim, the loader
    # normalizes it (lowercase + strip accents) before hitting the index.
    assert lookup("Intermarché", "ZZZNONEXISTENT") is None


def test_make_store_by_code_returns_dict_on_hit(db):
    """When a store with matching (retailer, store_code) exists, returns
    a dict with id (UUID) + name + address + score=1.0."""
    import uuid as _uuid
    from decimal import Decimal as _Decimal

    from ratis_core.models.store import Store

    store = Store(
        id=_uuid.uuid4(),
        name="Intermarché Test",
        retailer="intermarche",
        address="1 rue Test",
        city="Paris",
        postal_code="75001",
        store_code="UNIT_TEST_CODE",
        lat=_Decimal("48.85"),
        lng=_Decimal("2.35"),
    )
    db.add(store)
    db.flush()
    db.commit()

    lookup = orchestrator._make_store_by_code(db)
    out = lookup("intermarche", "UNIT_TEST_CODE")
    assert out is not None
    assert out["id"] == store.id
    assert out["name"] == "Intermarché Test"
    assert out["score"] == 1.0


def test_make_store_by_code_skips_disabled_stores(db):
    """Soft-deleted stores (is_disabled=true) are excluded from the lookup."""
    import uuid as _uuid
    from datetime import datetime as _datetime
    from decimal import Decimal as _Decimal

    from ratis_core.models.store import Store

    store = Store(
        id=_uuid.uuid4(),
        name="Disabled Store",
        retailer="intermarche",
        address="x",
        city="x",
        postal_code="00000",
        store_code="DIS_TEST_CODE",
        is_disabled=True,
        # PG ``disabled_at_check`` : is_disabled=true ⇒ disabled_at NOT NULL.
        disabled_at=_datetime.now(UTC),
        lat=_Decimal("48.85"),
        lng=_Decimal("2.35"),
    )
    db.add(store)
    db.flush()
    db.commit()

    lookup = orchestrator._make_store_by_code(db)
    assert lookup("intermarche", "DIS_TEST_CODE") is None


# ── Real-DB smoke (integration) ──────────────────────────────────────────


@pytest.mark.integration
def test_orchestrator_smoke_with_stub_llm(db, monkeypatch):
    """End-to-end smoke : real DB lookups + stub LLM + stubbed Phase-1.

    Phase 1 is stubbed (we don't run PaddleOCR in unit tests — that's
    covered by test_extract.py § integration). Phase 2 runs for real
    against the StubLLMClient ; Phase 3 hits the real DB lookups ;
    Phase 4 persists.
    """
    # Provide a date-bearing OCR block so that comprehend's
    # ``_extract_purchased_at`` returns a non-null date. Post anti-fraud
    # PR3+PR4, a receipt with no parsed date is rejected (cf ARCH §
    # étape 4) AND a receipt older than ``consensus.ticket_max_age_days``
    # (7 days) is rejected too — anchor the date to "now - 1 day" so the
    # smoke test stays evergreen.
    _date_for_test = (datetime.now(UTC) - timedelta(days=1)).date()
    date_text = f"TICKET DU {_date_for_test.strftime('%d/%m/%Y')} 14:30"
    date_block = RawBlock(
        text=date_text,
        bbox=(0.0, 0.0, 1.0, 1.0),
        confidence=0.99,
        content_hash=compute_block_hash(date_text, (0.0, 0.0, 1.0, 1.0), 0.99),
    )
    raw = RawTicket(
        receipt_id=uuid.uuid4(),
        blocks=(date_block,),
        barcodes=(),
        image_hash="b" * 64,
        ocr_engine_version="paddleocr-test-fr",
        captured_at=CAPTURED_AT,
    )
    monkeypatch.setattr(
        orchestrator.extract,
        "extract_raw_ticket",
        lambda image_bytes, **kwargs: raw,
    )

    canned = {
        "header": {"brand": "INTERMARCHE", "postcode": "92400", "city": "COURBEVOIE"},
        "footer": {"total_cents": 100, "vat_breakdown": []},
        "items": [
            {"raw_label": "BANANE", "quantity": 1, "total_cents": 100},
        ],
    }
    stub = StubLLMClient(canned)

    from ratis_core.identifiers import generate_support_id

    user = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users (id, email, support_id, display_name, account_type, "
            "                  is_deleted) "
            "VALUES (:id, :email, :sid, 'X', 'oauth', false)"
        ),
        {
            "id": user,
            "email": f"smoke-{uuid.uuid4()}@ratis.fr",
            "sid": generate_support_id(),
        },
    )
    db.commit()

    result = orchestrator.run_pipeline(
        b"image-bytes",
        db=db,
        user_id=user,
        captured_at=CAPTURED_AT,
        llm_client=stub,
    )
    db.commit()

    assert len(result["scan_ids"]) == 1
    assert result["parsed_ticket_id"] is not None
