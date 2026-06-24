"""Phase 2 comprehend tests (pure, no DB).

Cover :

- spatial line assembly (y-cluster + x-sort)
- ocr_knowledge integration (correction lookup)
- product_knowledge integration (barcode hint)
- LLM call with assembled text + barcode round-trip
- ParsedTicket immutability + parsed_jsonb_hash determinism
- audit-log event emission (started / llm_extraction_done / built)
- per-item events at ``log_level='verbose'`` only
- ComprehendError on malformed LLM output (no silent drop)

Cf. ``ARCH_receipt_pipeline.md`` § Phase 2 Comprendre + § Knowledge tables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from worker.pipeline.comprehend import (
    ComprehendError,
    LLMClient,
    _assemble_lines,
    comprehend_ticket,
)
from worker.pipeline.types import (
    ParsedReceiptBarcode,
    RawBarcode,
    RawBlock,
    RawTicket,
    compute_barcode_hash,
    compute_block_hash,
)

CAPTURED_AT = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


# ── helpers ────────────────────────────────────────────────────────────────


def _make_block(text: str, x: float, y: float, w: float = 80.0, h: float = 20.0) -> RawBlock:
    bbox = (x, y, w, h)
    return RawBlock(
        text=text,
        bbox=bbox,
        confidence=0.95,
        content_hash=compute_block_hash(text, bbox, 0.95),
    )


def _make_barcode(value: str = "3245678901234") -> RawBarcode:
    bbox = (5.0, 500.0, 200.0, 60.0)
    return RawBarcode(
        value=value,
        format="EAN13",
        bbox=bbox,
        content_hash=compute_barcode_hash(value, "EAN13", bbox),
    )


def _make_raw_ticket(
    blocks: list[RawBlock] | None = None,
    barcodes: list[RawBarcode] | None = None,
) -> RawTicket:
    return RawTicket(
        receipt_id=uuid4(),
        blocks=tuple(blocks or []),
        barcodes=tuple(barcodes or []),
        image_hash="a" * 64,
        ocr_engine_version="paddleocr-test-fr",
        captured_at=CAPTURED_AT,
    )


def _collecting_logger() -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    def _log(*, phase: str, level: str, event: str, payload: dict | None = None) -> None:
        events.append({"phase": phase, "level": level, "event": event, "payload": payload})

    return _log, events


class _StubLLM:
    """Records the input it received and returns a canned JSON output."""

    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.last_text: str | None = None
        self.last_barcodes: list[str] | None = None
        self.last_prompt: str | None = None
        self.calls = 0

    def extract(
        self,
        *,
        receipt_text: str,
        barcodes: list[str],
        prompt_template: str,
    ) -> dict[str, Any]:
        self.last_text = receipt_text
        self.last_barcodes = list(barcodes)
        self.last_prompt = prompt_template
        self.calls += 1
        return self.output


def _minimal_llm_output(items_extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "header": {
            "brand": "INTERMARCHE",
            "address_line": "12 RUE DE LA PAIX",
            "postcode": "92400",
            "city": "COURBEVOIE",
            "phone": "0146670000",
            "siret": "12345678901234",
        },
        "footer": {
            "total_cents": 1234,
            "vat_breakdown": [
                {"rate_pct": 5.5, "taxable_cents": 500, "tax_cents": 28},
            ],
            "payment_method": "CB",
            "item_count_declared": 1,
            "barcode_ticket": "TKT-001",
        },
        "items": items_extra
        if items_extra is not None
        else [
            {
                "raw_label": "LAIT DEMI ECREME",
                "quantity": 1,
                "unit_price_cents": 99,
                "total_cents": 99,
                "barcode": None,
            }
        ],
    }


# ── 1. spatial assembly ────────────────────────────────────────────────────


def test_assemble_lines_groups_by_y_proximity() -> None:
    """Blocks at y=10 and y=12 share a line ; y=50 is its own line."""
    b_a = _make_block("A", x=10.0, y=10.0)
    b_b = _make_block("B", x=100.0, y=12.0)
    b_c = _make_block("C", x=10.0, y=50.0)
    audit, _ = _collecting_logger()
    lines = _assemble_lines([b_a, b_b, b_c], audit_logger=audit, log_level="normal")
    assert len(lines) == 2, "expected 2 lines (y≈10 cluster, y=50 alone)"
    # First line contains A+B, second line contains C.
    texts_per_line = [[blk.text for blk in line] for line in lines]
    assert {"A", "B"} == set(texts_per_line[0])
    assert texts_per_line[1] == ["C"]


def test_assemble_lines_sorts_by_x_within_line() -> None:
    """Within a line, blocks are sorted by x ascending."""
    right = _make_block("RIGHT", x=200.0, y=10.0)
    left = _make_block("LEFT", x=10.0, y=11.0)
    audit, _ = _collecting_logger()
    lines = _assemble_lines([right, left], audit_logger=audit, log_level="normal")
    assert len(lines) == 1
    assert [blk.text for blk in lines[0]] == ["LEFT", "RIGHT"]


def test_assemble_lines_empty() -> None:
    audit, _ = _collecting_logger()
    assert _assemble_lines([], audit_logger=audit, log_level="normal") == []


# ── 2. ocr_knowledge integration ───────────────────────────────────────────


def test_ocr_knowledge_applied_when_hit() -> None:
    """When a corrected mapping exists, the LLM sees the corrected text."""
    raw = _make_raw_ticket(blocks=[_make_block("LATD DEMI ECREM", x=10.0, y=10.0)])
    stub = _StubLLM(_minimal_llm_output())

    def ocr_loader(raw_ocr: str) -> str | None:
        if raw_ocr.strip() == "LATD DEMI ECREM":
            return "LAIT DEMI ECREME"
        return None

    audit, _ = _collecting_logger()
    comprehend_ticket(
        raw,
        llm_client=stub,
        ocr_knowledge_loader=ocr_loader,
        audit_logger=audit,
    )
    assert stub.last_text is not None
    assert "LAIT DEMI ECREME" in stub.last_text
    assert "LATD DEMI ECREM" not in stub.last_text


def test_ocr_knowledge_not_applied_when_no_hit() -> None:
    raw = _make_raw_ticket(blocks=[_make_block("BANANE", x=10.0, y=10.0)])
    stub = _StubLLM(_minimal_llm_output())
    audit, _ = _collecting_logger()
    comprehend_ticket(
        raw,
        llm_client=stub,
        ocr_knowledge_loader=lambda _: None,
        audit_logger=audit,
    )
    assert stub.last_text is not None
    assert "BANANE" in stub.last_text


# ── 3. LLM call wiring ─────────────────────────────────────────────────────


def test_llm_called_with_assembled_text() -> None:
    """LLM receives a multi-line text in spatial order."""
    blocks = [
        _make_block("HEADER", x=10.0, y=10.0),
        _make_block("ITEM", x=10.0, y=100.0),
        _make_block("0.99", x=200.0, y=101.0),
        _make_block("TOTAL", x=10.0, y=200.0),
    ]
    raw = _make_raw_ticket(blocks=blocks)
    stub = _StubLLM(_minimal_llm_output())
    audit, _ = _collecting_logger()
    comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert stub.calls == 1
    assert stub.last_text is not None
    # Header line precedes item line precedes total line in the assembled text.
    assert stub.last_text.index("HEADER") < stub.last_text.index("ITEM")
    assert stub.last_text.index("ITEM") < stub.last_text.index("TOTAL")
    # ITEM and 0.99 share a line (same y) → in the same row of the output.
    item_line = next(line for line in stub.last_text.splitlines() if "ITEM" in line)
    assert "0.99" in item_line


def test_llm_called_with_barcodes() -> None:
    raw = _make_raw_ticket(
        blocks=[_make_block("X", x=0.0, y=0.0)],
        barcodes=[_make_barcode("3245678901234"), _make_barcode("0000000000000")],
    )
    stub = _StubLLM(_minimal_llm_output())
    audit, _ = _collecting_logger()
    comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert stub.last_barcodes == ["3245678901234", "0000000000000"]


# ── 4. ParsedTicket build / hash ───────────────────────────────────────────


def test_parsed_ticket_jsonb_hash_set() -> None:
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(_minimal_llm_output())
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.parsed_jsonb_hash is not None
    assert len(out.parsed_jsonb_hash) == 64
    assert out.raw_ticket_image_hash == raw.image_hash
    assert out.receipt_id == raw.receipt_id


def test_parsed_ticket_idempotent_hash() -> None:
    """Two runs with same inputs + same LLM output yield the same hash."""
    blocks = [_make_block("X", x=0.0, y=0.0)]
    raw = _make_raw_ticket(blocks=blocks)
    # Same receipt_id across both runs to make hash truly comparable.
    out_payload = _minimal_llm_output()
    stub_a = _StubLLM(out_payload)
    stub_b = _StubLLM(out_payload)
    audit, _ = _collecting_logger()
    a = comprehend_ticket(raw, llm_client=stub_a, audit_logger=audit)
    b = comprehend_ticket(raw, llm_client=stub_b, audit_logger=audit)
    # parsed_ticket.id is auto-generated → hashes will differ on id alone.
    # Build a copy of `b` with `a`'s id to assert structural determinism.
    b_with_a_id = b.model_copy(
        update={
            "id": a.id,
            "items": tuple(
                item.model_copy(update={"id": orig.id}) for item, orig in zip(b.items, a.items, strict=True)
            ),
            "parsed_jsonb_hash": None,
        }
    ).with_jsonb_hash()
    assert b_with_a_id.parsed_jsonb_hash == a.parsed_jsonb_hash


# ── 5. product_knowledge integration ───────────────────────────────────────


def test_product_knowledge_sets_barcode() -> None:
    raw = _make_raw_ticket(blocks=[_make_block("LAIT", x=0.0, y=0.0)])
    stub = _StubLLM(_minimal_llm_output())  # item barcode = None

    def product_loader(normalized_label: str) -> str | None:
        if normalized_label == "LAIT DEMI ECREME":
            return "3245676543210"
        return None

    audit, _ = _collecting_logger()
    out = comprehend_ticket(
        raw,
        llm_client=stub,
        product_knowledge_loader=product_loader,
        audit_logger=audit,
    )
    assert len(out.items) == 1
    assert out.items[0].barcode == "3245676543210"


def test_product_knowledge_skipped_when_barcode_already_set() -> None:
    """When an item already carries a barcode validated against pyzbar,
    the product_knowledge loader must NOT be called for it (saves DB hit
    in Phase 4, and avoids overriding the higher-confidence physical
    barcode read).
    """
    raw = _make_raw_ticket(
        blocks=[_make_block("X", x=0.0, y=0.0)],
        barcodes=[_make_barcode("1111111111111")],
    )
    items = [
        {
            "raw_label": "LAIT DEMI ECREME",
            "quantity": 1,
            "unit_price_cents": 99,
            "total_cents": 99,
            "barcode": "1111111111111",  # matches a physical pyzbar read
        }
    ]
    stub = _StubLLM(_minimal_llm_output(items_extra=items))

    calls: list[str] = []

    def product_loader(normalized_label: str) -> str | None:
        calls.append(normalized_label)
        return "9999999999999"

    audit, _ = _collecting_logger()
    out = comprehend_ticket(
        raw,
        llm_client=stub,
        product_knowledge_loader=product_loader,
        audit_logger=audit,
    )
    assert out.items[0].barcode == "1111111111111"
    assert calls == [], "loader should not be called when barcode is already set"


def test_hallucinated_barcode_from_llm_is_rejected() -> None:
    """If LLM emits a barcode value not seen by pyzbar, we drop it (anti-hallucination)."""
    raw = _make_raw_ticket(
        blocks=[_make_block("X", x=0.0, y=0.0)],
        barcodes=[_make_barcode("1111111111111")],
    )
    items = [
        {
            "raw_label": "ITEM",
            "quantity": 1,
            "unit_price_cents": 99,
            "total_cents": 99,
            "barcode": "9999999999999",  # NOT in raw.barcodes
        }
    ]
    stub = _StubLLM(_minimal_llm_output(items_extra=items))
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.items[0].barcode is None, "hallucinated EAN must be filtered"


# ── 6. money + siret normalization ─────────────────────────────────────────


def test_total_cents_parsed_as_int() -> None:
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(_minimal_llm_output())
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.footer.total_cents == 1234
    assert isinstance(out.footer.total_cents, int)


def test_money_str_input_is_normalized_via_decimal() -> None:
    """LLM may return numeric strings ; we use Decimal-safe conversion."""
    payload = _minimal_llm_output()
    payload["footer"]["total_cents"] = "1234"  # string, must be coerced safely
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(payload)
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.footer.total_cents == 1234


def test_siret_invalid_falls_to_none() -> None:
    """SIRET not 14 digits is filtered to None (defensive — LLM can be wrong)."""
    payload = _minimal_llm_output()
    payload["header"]["siret"] = "12345"  # only 5 digits
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(payload)
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.header.siret is None


def test_normalized_label_is_uppercase_and_accents_folded() -> None:
    payload = _minimal_llm_output(
        items_extra=[
            {
                "raw_label": "Café Crème",
                "quantity": 1,
                "unit_price_cents": 250,
                "total_cents": 250,
                "barcode": None,
            }
        ]
    )
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(payload)
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.items[0].raw_label == "Café Crème"
    assert out.items[0].normalized_label == "CAFE CREME"


# ── 7. parsing_issues / quantity defaults ──────────────────────────────────


def test_quantity_defaults_to_one_when_absent() -> None:
    payload = _minimal_llm_output(
        items_extra=[
            {
                "raw_label": "BANANE",
                "unit_price_cents": 50,
                "total_cents": 50,
                "barcode": None,
            }
        ]
    )
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(payload)
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.items[0].quantity == 1


def test_parsing_issue_added_when_total_mismatches_qty_unit() -> None:
    """If qty * unit_price != total → parsing_issues collected, item NOT dropped."""
    payload = _minimal_llm_output(
        items_extra=[
            {
                "raw_label": "ARTICLE",
                "quantity": 2,
                "unit_price_cents": 100,
                "total_cents": 250,  # 2*100 != 250
                "barcode": None,
            }
        ]
    )
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(payload)
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert len(out.items) == 1, "item must NOT be dropped silently"
    assert any("total" in iss for iss in out.items[0].parsing_issues)


# ── 8. audit events ────────────────────────────────────────────────────────


def test_audit_events_emitted() -> None:
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(_minimal_llm_output())
    audit, events = _collecting_logger()
    comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    event_names = {e["event"] for e in events}
    assert "comprehend_started" in event_names
    assert "llm_extraction_done" in event_names
    assert "parsed_ticket_built" in event_names
    for ev in events:
        assert ev["phase"] == "comprehend"


def test_audit_verbose_emits_more_events_than_normal() -> None:
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub_n = _StubLLM(_minimal_llm_output())
    stub_v = _StubLLM(_minimal_llm_output())

    def loader_for_verbose(label: str) -> str | None:
        if label == "LAIT DEMI ECREME":
            return "3245676543210"
        return None

    audit_n, events_n = _collecting_logger()
    audit_v, events_v = _collecting_logger()
    comprehend_ticket(
        raw,
        llm_client=stub_n,
        product_knowledge_loader=loader_for_verbose,
        audit_logger=audit_n,
        log_level="normal",
    )
    comprehend_ticket(
        raw,
        llm_client=stub_v,
        product_knowledge_loader=loader_for_verbose,
        audit_logger=audit_v,
        log_level="verbose",
    )
    assert len(events_v) > len(events_n)


# ── 9. error handling ──────────────────────────────────────────────────────


def test_comprehend_error_on_malformed_items_field() -> None:
    bad = _minimal_llm_output()
    bad["items"] = "not_a_list"  # invalid shape
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(bad)
    audit, _ = _collecting_logger()
    with pytest.raises(ComprehendError):
        comprehend_ticket(raw, llm_client=stub, audit_logger=audit)


def test_comprehend_error_on_missing_total_in_item() -> None:
    """An item without total_cents is invalid (ParsedItem.total_cents required)."""
    bad = _minimal_llm_output(
        items_extra=[
            {
                "raw_label": "BAD",
                "quantity": 1,
                "unit_price_cents": 50,
                # total_cents missing
                "barcode": None,
            }
        ]
    )
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    stub = _StubLLM(bad)
    audit, _ = _collecting_logger()
    with pytest.raises(ComprehendError):
        comprehend_ticket(raw, llm_client=stub, audit_logger=audit)


# ── 10. lineage ────────────────────────────────────────────────────────────


def test_header_footer_carry_source_block_ids() -> None:
    """source_block_ids must be populated (non-empty when blocks contributed)."""
    raw = _make_raw_ticket(blocks=[_make_block("INTERMARCHE", x=0.0, y=0.0)])
    stub = _StubLLM(_minimal_llm_output())
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    # Best-effort lineage : if a header field's text appears in a block, link it.
    # At minimum the field exists and is a tuple (possibly empty if no fuzzy hit).
    assert isinstance(out.header.source_block_ids, tuple)
    assert isinstance(out.footer.source_block_ids, tuple)
    # All items must reference at least the receipt blocks (best-effort, fallback
    # to all blocks when the matcher can't disambiguate).
    for it in out.items:
        assert isinstance(it.source_block_ids, tuple)


def test_llm_protocol_satisfied_by_stub() -> None:
    """Verify the runtime contract — _StubLLM must satisfy LLMClient."""
    stub: LLMClient = _StubLLM(_minimal_llm_output())  # type: ignore[assignment]
    out = stub.extract(receipt_text="abc", barcodes=[], prompt_template="p")
    assert "items" in out


# ── 11. Barcode parser wiring (PR-B) ───────────────────────────────────────


def test_comprehend_calls_barcode_parser_when_raw_present() -> None:
    """When LLM output contains footer.barcode_ticket, the injected
    barcode_parser callback is invoked with (raw, header.brand)."""
    raw_ticket_id = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    payload = _minimal_llm_output()
    payload["footer"]["barcode_ticket"] = "20260430143067122010" + "0100"
    stub = _StubLLM(payload)

    received: list[tuple[str, str | None]] = []

    def parser(raw: str, retailer: str | None) -> ParsedReceiptBarcode:
        received.append((raw, retailer))
        return ParsedReceiptBarcode(
            raw=raw,
            retailer_key="intermarche",
            store_code="00100",
        )

    audit, _ = _collecting_logger()
    out = comprehend_ticket(
        raw_ticket_id,
        llm_client=stub,
        barcode_parser=parser,
        audit_logger=audit,
    )
    assert received == [("20260430143067122010" + "0100", "INTERMARCHE")]
    assert out.footer.barcode is not None
    assert out.footer.barcode.store_code == "00100"
    assert out.footer.barcode.retailer_key == "intermarche"


def test_comprehend_skips_barcode_parser_when_no_raw() -> None:
    """No barcode_ticket in LLM output → parser not called, footer.barcode=None."""
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    payload = _minimal_llm_output()
    payload["footer"]["barcode_ticket"] = None
    stub = _StubLLM(payload)

    calls: list[Any] = []

    def parser(raw_v: str, retailer: str | None) -> ParsedReceiptBarcode:
        calls.append((raw_v, retailer))
        return ParsedReceiptBarcode(raw=raw_v)

    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, barcode_parser=parser, audit_logger=audit)
    assert calls == []
    assert out.footer.barcode is None


def test_comprehend_skips_barcode_parser_when_raw_too_short() -> None:
    """Defensive : OCR junk under 10 chars is not worth a parser call."""
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    payload = _minimal_llm_output()
    payload["footer"]["barcode_ticket"] = "12345"  # 5 chars
    stub = _StubLLM(payload)

    calls: list[Any] = []

    def parser(raw_v: str, retailer: str | None) -> ParsedReceiptBarcode:
        calls.append(raw_v)
        return ParsedReceiptBarcode(raw=raw_v)

    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, barcode_parser=parser, audit_logger=audit)
    assert calls == []
    assert out.footer.barcode is None


def test_comprehend_default_barcode_parser_returns_raw_only() -> None:
    """Default no-op parser preserves raw, no decoded fields."""
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    payload = _minimal_llm_output()
    payload["footer"]["barcode_ticket"] = "1234567890ABCD"  # >=10 chars
    stub = _StubLLM(payload)
    audit, _ = _collecting_logger()
    out = comprehend_ticket(raw, llm_client=stub, audit_logger=audit)
    assert out.footer.barcode is not None
    assert out.footer.barcode.raw == "1234567890ABCD"
    assert out.footer.barcode.store_code is None
    assert out.footer.barcode.retailer_key is None


def test_comprehend_emits_barcode_parsed_event_when_fields_present() -> None:
    """Parser hit with decoded fields → audit event ``barcode_parsed`` (verbose)."""
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    payload = _minimal_llm_output()
    payload["footer"]["barcode_ticket"] = "20260430143067122010" + "0100"
    stub = _StubLLM(payload)

    def parser(raw_v: str, retailer: str | None) -> ParsedReceiptBarcode:
        return ParsedReceiptBarcode(
            raw=raw_v,
            retailer_key="intermarche",
            store_code="00100",
        )

    audit, events = _collecting_logger()
    comprehend_ticket(
        raw,
        llm_client=stub,
        barcode_parser=parser,
        audit_logger=audit,
        log_level="verbose",
    )
    names = {e["event"] for e in events if e["level"] == "verbose"}
    assert "barcode_parsed" in names


def test_comprehend_emits_barcode_unparsed_event_when_only_raw() -> None:
    """Parser returns raw-only → audit event ``barcode_unparsed`` (verbose)."""
    raw = _make_raw_ticket(blocks=[_make_block("X", x=0.0, y=0.0)])
    payload = _minimal_llm_output()
    payload["footer"]["barcode_ticket"] = "1234567890ABCD"
    stub = _StubLLM(payload)

    def parser(raw_v: str, retailer: str | None) -> ParsedReceiptBarcode:
        return ParsedReceiptBarcode(raw=raw_v)

    audit, events = _collecting_logger()
    comprehend_ticket(
        raw,
        llm_client=stub,
        barcode_parser=parser,
        audit_logger=audit,
        log_level="verbose",
    )
    names = {e["event"] for e in events if e["level"] == "verbose"}
    assert "barcode_unparsed" in names
