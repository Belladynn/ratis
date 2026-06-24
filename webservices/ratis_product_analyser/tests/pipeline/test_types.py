"""Unit tests for pipeline Pydantic v2 types.

Pure type-contract tests — no DB, no I/O, no fixtures. These tests
guard the contract that all subsequent pipeline blocks (parser,
matcher, persistence) will depend on. They cover :

- Happy-path construction of every model.
- UUID auto-generation when not provided, preserved when provided.
- ``content_hash`` reproducibility (same inputs → same hash) and
  sensitivity (single-field change → different hash).
- Frozen behaviour (mutation post-construction raises).
- :class:`ParsedHeader` SIRET shape validation.
- :class:`ParsedFooter` total / VAT breakdown semantics.
- :class:`Candidate` / :class:`DecisionInputs` audit types.
- ``ItemMatch`` invariants : ``status``/``match_method``/
  ``rejected_reason``/``match_confidence`` cross-field rules + the
  ``top_candidates`` (max 5) and ``decision_inputs`` (required) cap.
- ``MatchedTicket`` invariants : ``store_status``/``store_match_id``
  (UUID-typed) / ``store_rejected_reason`` cross-field rules.
- ``ParsedTicket.with_jsonb_hash`` : returns a new instance, hash
  is reproducible across calls, sensitive to item changes.
- ``compute_image_hash`` reproducibility + sensitivity.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
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
    RawBarcode,
    RawBlock,
    RawTicket,
    VatLine,
    compute_barcode_hash,
    compute_block_hash,
    compute_image_hash,
    compute_parsed_jsonb_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision_inputs(**overrides) -> DecisionInputs:
    """Build a ``DecisionInputs`` with sensible defaults for tests that
    don't care about the audit snapshot specifics."""
    base = {
        "normalized_label": "HIPRO VANILLE",
        "barcode_used": None,
        "knowledge_lookup_hit": False,
        "consensus_state": None,
        "candidates_considered": 0,
    }
    base.update(overrides)
    return DecisionInputs(**base)


# ---------------------------------------------------------------------------
# Phase 1 — RawBlock / RawBarcode / RawTicket
# ---------------------------------------------------------------------------


class TestRawBlock:
    def test_construction_happy_path(self):
        block = RawBlock(
            text="HIPRO Vanille",
            bbox=(10.0, 20.0, 100.0, 40.0),
            confidence=0.92,
            content_hash=compute_block_hash("HIPRO Vanille", (10.0, 20.0, 100.0, 40.0), 0.92),
        )
        assert block.text == "HIPRO Vanille"
        assert block.bbox == (10.0, 20.0, 100.0, 40.0)
        assert block.confidence == 0.92
        assert isinstance(block.id, UUID)
        assert len(block.content_hash) == 64  # sha256 hex

    def test_uuid_auto_generated_unique(self):
        b1 = RawBlock(
            text="X",
            bbox=(0.0, 0.0, 1.0, 1.0),
            confidence=0.5,
            content_hash=compute_block_hash("X", (0.0, 0.0, 1.0, 1.0), 0.5),
        )
        b2 = RawBlock(
            text="X",
            bbox=(0.0, 0.0, 1.0, 1.0),
            confidence=0.5,
            content_hash=compute_block_hash("X", (0.0, 0.0, 1.0, 1.0), 0.5),
        )
        assert b1.id != b2.id

    def test_uuid_preserved_when_provided(self):
        my_id = uuid4()
        block = RawBlock(
            id=my_id,
            text="X",
            bbox=(0.0, 0.0, 1.0, 1.0),
            confidence=0.5,
            content_hash=compute_block_hash("X", (0.0, 0.0, 1.0, 1.0), 0.5),
        )
        assert block.id == my_id

    def test_content_hash_reproducible(self):
        h1 = compute_block_hash("HIPRO", (1.0, 2.0, 3.0, 4.0), 0.9123456)
        h2 = compute_block_hash("HIPRO", (1.0, 2.0, 3.0, 4.0), 0.9123456)
        assert h1 == h2

    def test_content_hash_sensitive_to_text(self):
        h1 = compute_block_hash("HIPRO", (1.0, 2.0, 3.0, 4.0), 0.9)
        h2 = compute_block_hash("HIPRP", (1.0, 2.0, 3.0, 4.0), 0.9)
        assert h1 != h2

    def test_content_hash_sensitive_to_bbox(self):
        h1 = compute_block_hash("X", (1.0, 2.0, 3.0, 4.0), 0.9)
        h2 = compute_block_hash("X", (1.0, 2.0, 3.0, 5.0), 0.9)
        assert h1 != h2

    def test_content_hash_sensitive_to_confidence(self):
        h1 = compute_block_hash("X", (1.0, 2.0, 3.0, 4.0), 0.9)
        h2 = compute_block_hash("X", (1.0, 2.0, 3.0, 4.0), 0.8)
        assert h1 != h2

    def test_content_hash_rounds_confidence_to_4_decimals(self):
        # Per docstring : confidence is rounded to 4 decimals before hashing
        # so floating-point noise (0.91234567 vs 0.91234568) doesn't break
        # reproducibility.
        h1 = compute_block_hash("X", (0.0, 0.0, 1.0, 1.0), 0.91234567)
        h2 = compute_block_hash("X", (0.0, 0.0, 1.0, 1.0), 0.91234568)
        assert h1 == h2

    def test_frozen_blocks_mutation(self):
        block = RawBlock(
            text="X",
            bbox=(0.0, 0.0, 1.0, 1.0),
            confidence=0.5,
            content_hash=compute_block_hash("X", (0.0, 0.0, 1.0, 1.0), 0.5),
        )
        with pytest.raises((ValidationError, TypeError)):
            block.text = "Y"

    def test_confidence_must_be_in_range(self):
        with pytest.raises(ValidationError):
            RawBlock(
                text="X",
                bbox=(0.0, 0.0, 1.0, 1.0),
                confidence=1.5,
                content_hash="0" * 64,
            )
        with pytest.raises(ValidationError):
            RawBlock(
                text="X",
                bbox=(0.0, 0.0, 1.0, 1.0),
                confidence=-0.1,
                content_hash="0" * 64,
            )


class TestRawBarcode:
    def test_construction_happy_path(self):
        bc = RawBarcode(
            value="3760000000017",
            format="EAN13",
            bbox=(50.0, 800.0, 200.0, 50.0),
            content_hash=compute_barcode_hash("3760000000017", "EAN13", (50.0, 800.0, 200.0, 50.0)),
        )
        assert bc.value == "3760000000017"
        assert bc.format == "EAN13"

    def test_content_hash_reproducible(self):
        h1 = compute_barcode_hash("123", "EAN13", (1.0, 2.0, 3.0, 4.0))
        h2 = compute_barcode_hash("123", "EAN13", (1.0, 2.0, 3.0, 4.0))
        assert h1 == h2

    def test_content_hash_sensitive(self):
        h1 = compute_barcode_hash("123", "EAN13", (1.0, 2.0, 3.0, 4.0))
        h2 = compute_barcode_hash("124", "EAN13", (1.0, 2.0, 3.0, 4.0))
        h3 = compute_barcode_hash("123", "EAN8", (1.0, 2.0, 3.0, 4.0))
        h4 = compute_barcode_hash("123", "EAN13", (1.0, 2.0, 3.0, 5.0))
        assert len({h1, h2, h3, h4}) == 4

    def test_frozen(self):
        bc = RawBarcode(
            value="123",
            format="EAN13",
            bbox=(0.0, 0.0, 1.0, 1.0),
            content_hash="0" * 64,
        )
        with pytest.raises((ValidationError, TypeError)):
            bc.value = "999"


class TestRawTicket:
    def _make_block(self, text: str = "X") -> RawBlock:
        return RawBlock(
            text=text,
            bbox=(0.0, 0.0, 1.0, 1.0),
            confidence=0.9,
            content_hash=compute_block_hash(text, (0.0, 0.0, 1.0, 1.0), 0.9),
        )

    def test_construction_happy_path(self):
        receipt_id = uuid4()
        ticket = RawTicket(
            receipt_id=receipt_id,
            blocks=[self._make_block("A"), self._make_block("B")],
            barcodes=[],
            image_hash="a" * 64,
            ocr_engine_version="paddleocr-2.7.3-fr",
            captured_at=datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC),
        )
        assert ticket.receipt_id == receipt_id
        assert len(ticket.blocks) == 2
        assert ticket.barcodes == ()  # tuple after frozen-coercion or list
        assert ticket.ocr_engine_version == "paddleocr-2.7.3-fr"

    def test_frozen(self):
        ticket = RawTicket(
            receipt_id=uuid4(),
            blocks=[self._make_block("A")],
            barcodes=[],
            image_hash="a" * 64,
            ocr_engine_version="paddleocr-2.7.3-fr",
            captured_at=datetime.now(tz=UTC),
        )
        with pytest.raises((ValidationError, TypeError)):
            ticket.ocr_engine_version = "other"


# ---------------------------------------------------------------------------
# Phase 2 — VatLine / ParsedHeader / ParsedFooter / ParsedItem / ParsedTicket
# ---------------------------------------------------------------------------


class TestVatLine:
    def test_construction_happy_path(self):
        bid = uuid4()
        line = VatLine(
            rate_pct=20.0,
            taxable_cents=1000,
            tax_cents=200,
            source_block_ids=[bid],
        )
        assert line.rate_pct == 20.0
        assert line.taxable_cents == 1000
        assert line.tax_cents == 200
        assert line.source_block_ids == (bid,)

    def test_negative_taxable_rejected(self):
        with pytest.raises(ValidationError):
            VatLine(
                rate_pct=20.0,
                taxable_cents=-1,
                tax_cents=0,
                source_block_ids=[],
            )

    def test_negative_tax_rejected(self):
        with pytest.raises(ValidationError):
            VatLine(
                rate_pct=20.0,
                taxable_cents=1000,
                tax_cents=-1,
                source_block_ids=[],
            )

    def test_frozen(self):
        line = VatLine(rate_pct=5.5, taxable_cents=100, tax_cents=5, source_block_ids=[])
        with pytest.raises((ValidationError, TypeError)):
            line.rate_pct = 10.0


class TestParsedHeader:
    def test_all_optional_none_ok(self):
        bid = uuid4()
        header = ParsedHeader(source_block_ids=[bid])
        assert header.brand is None
        assert header.address_line is None
        assert header.postcode is None
        assert header.city is None
        assert header.phone is None
        assert header.siret is None
        assert header.source_block_ids == (bid,)

    def test_full_population_ok(self):
        header = ParsedHeader(
            brand="INTERMARCHE",
            address_line="12 rue de Paris",
            postcode="92400",
            city="COURBEVOIE",
            phone="0140000000",
            siret="12345678901234",
            source_block_ids=[uuid4()],
        )
        assert header.brand == "INTERMARCHE"
        assert header.siret == "12345678901234"

    def test_siret_valid_14_digits(self):
        header = ParsedHeader(siret="12345678901234", source_block_ids=[])
        assert header.siret == "12345678901234"

    def test_siret_13_digits_rejected(self):
        with pytest.raises(ValidationError):
            ParsedHeader(siret="1234567890123", source_block_ids=[])

    def test_siret_15_digits_rejected(self):
        with pytest.raises(ValidationError):
            ParsedHeader(siret="123456789012345", source_block_ids=[])

    def test_siret_with_letters_rejected(self):
        with pytest.raises(ValidationError):
            ParsedHeader(siret="1234567890123A", source_block_ids=[])

    def test_siret_with_spaces_rejected(self):
        with pytest.raises(ValidationError):
            ParsedHeader(siret="123 456 789 01234", source_block_ids=[])

    def test_frozen(self):
        header = ParsedHeader(source_block_ids=[])
        with pytest.raises((ValidationError, TypeError)):
            header.brand = "X"


class TestParsedFooter:
    def test_construction_happy_path_empty_vat(self):
        footer = ParsedFooter(
            total_cents=1234,
            vat_breakdown=(),
            payment_method="CB",
            item_count_declared=3,
            barcode=ParsedReceiptBarcode(raw="T-2026-04-30-001"),
            source_block_ids=[uuid4()],
        )
        assert footer.total_cents == 1234
        assert footer.vat_breakdown == ()
        assert footer.payment_method == "CB"
        assert footer.item_count_declared == 3
        assert footer.barcode is not None
        assert footer.barcode.raw == "T-2026-04-30-001"

    def test_all_optional_none_ok(self):
        footer = ParsedFooter(source_block_ids=[])
        assert footer.total_cents is None
        assert footer.vat_breakdown == ()
        assert footer.payment_method is None
        assert footer.item_count_declared is None
        assert footer.barcode is None

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            ParsedFooter(total_cents=-1, source_block_ids=[])

    def test_with_two_vat_lines(self):
        line_55 = VatLine(rate_pct=5.5, taxable_cents=500, tax_cents=27, source_block_ids=[])
        line_20 = VatLine(rate_pct=20.0, taxable_cents=2000, tax_cents=400, source_block_ids=[])
        footer = ParsedFooter(
            total_cents=2927,
            vat_breakdown=(line_55, line_20),
            source_block_ids=[],
        )
        assert len(footer.vat_breakdown) == 2
        assert footer.vat_breakdown[0].rate_pct == 5.5
        assert footer.vat_breakdown[1].rate_pct == 20.0

    def test_frozen(self):
        footer = ParsedFooter(source_block_ids=[])
        with pytest.raises((ValidationError, TypeError)):
            footer.total_cents = 100


class TestParsedItem:
    def test_construction_happy_path(self):
        item = ParsedItem(
            raw_label="hipro vanille",
            normalized_label="HIPRO VANILLE",
            quantity=2,
            unit_price_cents=199,
            total_cents=398,
            barcode=None,
            source_block_ids=[uuid4(), uuid4()],
            parsing_issues=[],
        )
        assert isinstance(item.id, UUID)
        assert item.quantity == 2
        assert item.parsing_issues == ()

    def test_quantity_default_is_1(self):
        item = ParsedItem(
            raw_label="x",
            normalized_label="X",
            total_cents=100,
            source_block_ids=[],
            parsing_issues=[],
        )
        assert item.quantity == 1

    def test_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            ParsedItem(
                raw_label="x",
                normalized_label="X",
                quantity=0,
                total_cents=100,
                source_block_ids=[],
                parsing_issues=[],
            )

    def test_frozen(self):
        item = ParsedItem(
            raw_label="x",
            normalized_label="X",
            total_cents=100,
            source_block_ids=[],
            parsing_issues=[],
        )
        with pytest.raises((ValidationError, TypeError)):
            item.raw_label = "y"


class TestParsedTicket:
    def _minimal_ticket(self) -> ParsedTicket:
        receipt_id = uuid4()
        item = ParsedItem(
            raw_label="hipro",
            normalized_label="HIPRO",
            quantity=1,
            total_cents=100,
            source_block_ids=[],
            parsing_issues=[],
        )
        header = ParsedHeader(brand="INTER", source_block_ids=[])
        footer = ParsedFooter(total_cents=100, source_block_ids=[])
        return ParsedTicket(
            receipt_id=receipt_id,
            items=[item],
            header=header,
            footer=footer,
            purchased_at=None,
            raw_ticket_image_hash="a" * 64,
        )

    def test_construction_happy_path(self):
        ticket = self._minimal_ticket()
        assert isinstance(ticket.id, UUID)
        assert ticket.parsed_jsonb_hash is None
        assert len(ticket.items) == 1
        assert ticket.header.brand == "INTER"
        assert ticket.footer.total_cents == 100

    def test_with_jsonb_hash_sets_hash_and_returns_new_instance(self):
        t1 = self._minimal_ticket()
        t2 = t1.with_jsonb_hash()
        assert t1 is not t2  # new instance
        assert t1.parsed_jsonb_hash is None
        assert t2.parsed_jsonb_hash is not None
        assert len(t2.parsed_jsonb_hash) == 64

    def test_with_jsonb_hash_reproducible(self):
        t1 = self._minimal_ticket()
        h1 = t1.with_jsonb_hash().parsed_jsonb_hash
        h2 = t1.with_jsonb_hash().parsed_jsonb_hash
        assert h1 == h2

    def test_with_jsonb_hash_excludes_self(self):
        # Calling with_jsonb_hash on an already-hashed ticket recomputes
        # the same hash (the field itself is excluded from hashing).
        t1 = self._minimal_ticket().with_jsonb_hash()
        t2 = t1.with_jsonb_hash()
        assert t1.parsed_jsonb_hash == t2.parsed_jsonb_hash

    def test_with_jsonb_hash_stable_with_full_header_footer(self):
        # Ensure the hash is computable when ParsedHeader / ParsedFooter
        # carry their full-feature population (covers serializer parity
        # for VatLine + UUID source_block_ids).
        bid = uuid4()
        item = ParsedItem(
            raw_label="x",
            normalized_label="X",
            total_cents=100,
            source_block_ids=[bid],
            parsing_issues=[],
        )
        header = ParsedHeader(
            brand="INTERMARCHE",
            address_line="12 rue X",
            postcode="92400",
            city="COURBEVOIE",
            phone="0140000000",
            siret="12345678901234",
            source_block_ids=[bid],
        )
        footer = ParsedFooter(
            total_cents=100,
            vat_breakdown=(VatLine(rate_pct=20.0, taxable_cents=83, tax_cents=17, source_block_ids=[bid]),),
            payment_method="CB",
            item_count_declared=1,
            barcode=ParsedReceiptBarcode(raw="T-1"),
            source_block_ids=[bid],
        )
        ticket = ParsedTicket(
            receipt_id=uuid4(),
            items=[item],
            header=header,
            footer=footer,
            raw_ticket_image_hash="a" * 64,
        )
        h1 = ticket.with_jsonb_hash().parsed_jsonb_hash
        h2 = ticket.with_jsonb_hash().parsed_jsonb_hash
        assert h1 == h2
        assert len(h1) == 64

    def test_jsonb_hash_sensitive_to_item_change(self):
        t1 = self._minimal_ticket().with_jsonb_hash()
        # Build a different ticket with a different item label
        receipt_id = t1.receipt_id
        item2 = ParsedItem(
            raw_label="other",
            normalized_label="OTHER",
            quantity=1,
            total_cents=100,
            source_block_ids=[],
            parsing_issues=[],
        )
        t2 = ParsedTicket(
            id=t1.id,
            receipt_id=receipt_id,
            items=[item2],
            header=t1.header,
            footer=t1.footer,
            purchased_at=t1.purchased_at,
            raw_ticket_image_hash=t1.raw_ticket_image_hash,
        ).with_jsonb_hash()
        assert t1.parsed_jsonb_hash != t2.parsed_jsonb_hash

    def test_frozen(self):
        ticket = self._minimal_ticket()
        with pytest.raises((ValidationError, TypeError)):
            ticket.raw_ticket_image_hash = "z" * 64


# ---------------------------------------------------------------------------
# Phase 3 — Candidate / DecisionInputs / ItemMatch / MatchedTicket invariants
# ---------------------------------------------------------------------------


class TestCandidate:
    def test_construction_happy_path(self):
        cand = Candidate(
            product_ean="3760000000017",
            product_label="HIPRO VANILLE 125G",
            score=0.97,
            source="consensus_match",
        )
        assert cand.product_ean == "3760000000017"
        assert cand.score == 0.97
        assert cand.source == "consensus_match"

    def test_score_above_one_rejected(self):
        with pytest.raises(ValidationError):
            Candidate(
                product_ean="X",
                product_label="X",
                score=1.01,
                source="consensus_match",
            )

    def test_score_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            Candidate(
                product_ean="X",
                product_label="X",
                score=-0.01,
                source="consensus_match",
            )

    def test_score_boundaries_ok(self):
        Candidate(product_ean="X", product_label="X", score=0.0, source="barcode")
        Candidate(product_ean="X", product_label="X", score=1.0, source="barcode")

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            Candidate(
                product_ean="X",
                product_label="X",
                score=0.9,
                source="random",  # type: ignore[arg-type]
            )

    def test_frozen(self):
        cand = Candidate(product_ean="X", product_label="X", score=0.9, source="barcode")
        with pytest.raises((ValidationError, TypeError)):
            cand.score = 0.8


class TestDecisionInputs:
    def test_construction_happy_path(self):
        di = DecisionInputs(
            normalized_label="HIPRO VANILLE",
            barcode_used="3760000000017",
            knowledge_lookup_hit=True,
            consensus_state=None,
            candidates_considered=4,
        )
        assert di.normalized_label == "HIPRO VANILLE"
        assert di.barcode_used == "3760000000017"
        assert di.knowledge_lookup_hit is True
        assert di.candidates_considered == 4

    def test_barcode_used_optional(self):
        di = DecisionInputs(
            normalized_label="X",
            knowledge_lookup_hit=False,
            consensus_state=None,
            candidates_considered=0,
        )
        assert di.barcode_used is None

    def test_consensus_state_recorded(self):
        di = DecisionInputs(
            normalized_label="X",
            knowledge_lookup_hit=False,
            consensus_state="VERIFIED",
            candidates_considered=1,
        )
        assert di.consensus_state == "VERIFIED"

    def test_negative_candidates_rejected(self):
        with pytest.raises(ValidationError):
            DecisionInputs(
                normalized_label="X",
                knowledge_lookup_hit=False,
                consensus_state=None,
                candidates_considered=-1,
            )

    def test_frozen(self):
        di = _make_decision_inputs()
        with pytest.raises((ValidationError, TypeError)):
            di.consensus_state = "PENDING"


class TestItemMatchInvariants:
    def test_matched_with_ean_and_method_ok(self):
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="3760000000017",
            match_method="barcode",
            match_confidence=1.0,
            rejected_reason=None,
            decision_inputs=_make_decision_inputs(),
        )
        assert match.status == "matched"

    def test_matched_without_ean_fails(self):
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="matched",
                product_ean=None,
                match_method="barcode",
                rejected_reason=None,
                decision_inputs=_make_decision_inputs(),
            )

    def test_matched_without_method_fails(self):
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="matched",
                product_ean="3760000000017",
                match_method=None,
                rejected_reason=None,
                decision_inputs=_make_decision_inputs(),
            )

    def test_unresolved_without_reason_fails(self):
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="unresolved",
                product_ean=None,
                match_method=None,
                rejected_reason=None,
                decision_inputs=_make_decision_inputs(),
            )

    def test_rejected_without_reason_fails(self):
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="rejected",
                product_ean=None,
                match_method=None,
                rejected_reason=None,
                decision_inputs=_make_decision_inputs(),
            )

    def test_matched_with_rejected_reason_ok(self):
        # A matched item may still carry a rejected_reason as a debug
        # comment — the inverse invariant is intentionally not enforced.
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="3760000000017",
            match_method="knowledge",
            rejected_reason="kept-for-debug-context",
            decision_inputs=_make_decision_inputs(),
        )
        assert match.rejected_reason == "kept-for-debug-context"

    def test_match_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="matched",
                product_ean="X",
                match_method="barcode",
                match_confidence=1.5,
                decision_inputs=_make_decision_inputs(),
            )
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="matched",
                product_ean="X",
                match_method="barcode",
                match_confidence=-0.1,
                rejected_reason=None,
                decision_inputs=_make_decision_inputs(),
            )

    def test_match_confidence_none_ok(self):
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="X",
            match_method="barcode",
            match_confidence=None,
            decision_inputs=_make_decision_inputs(),
        )
        assert match.match_confidence is None

    def test_missing_decision_inputs_fails(self):
        # decision_inputs is required (not Optional) — even rejected
        # ItemMatch must record the inputs that led to the decision.
        with pytest.raises(ValidationError):
            ItemMatch(  # type: ignore[call-arg]
                parsed_item_id=uuid4(),
                status="matched",
                product_ean="X",
                match_method="barcode",
            )

    def test_top_candidates_default_empty_tuple(self):
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="X",
            match_method="barcode",
            decision_inputs=_make_decision_inputs(),
        )
        assert match.top_candidates == ()

    def test_top_candidates_with_5_ok(self):
        cands = tuple(
            Candidate(product_ean=f"E{i}", product_label=f"L{i}", score=0.9, source="consensus_match") for i in range(5)
        )
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="E0",
            match_method="consensus_match",
            top_candidates=cands,
            decision_inputs=_make_decision_inputs(candidates_considered=20),
        )
        assert len(match.top_candidates) == 5

    def test_top_candidates_with_6_rejected(self):
        cands = tuple(
            Candidate(product_ean=f"E{i}", product_label=f"L{i}", score=0.9, source="consensus_match") for i in range(6)
        )
        with pytest.raises(ValidationError):
            ItemMatch(
                parsed_item_id=uuid4(),
                status="matched",
                product_ean="E0",
                match_method="consensus_match",
                top_candidates=cands,
                decision_inputs=_make_decision_inputs(candidates_considered=20),
            )

    def test_matched_with_top_candidates_containing_chosen_ok(self):
        chosen = Candidate(
            product_ean="3760000000017",
            product_label="HIPRO VANILLE",
            score=0.99,
            source="consensus_match",
        )
        runner_up = Candidate(
            product_ean="3760000000024",
            product_label="HIPRO CHOCOLAT",
            score=0.82,
            source="consensus_match",
        )
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="3760000000017",
            match_method="consensus_match",
            match_confidence=0.99,
            top_candidates=(chosen, runner_up),
            decision_inputs=_make_decision_inputs(candidates_considered=2),
        )
        assert match.top_candidates[0].product_ean == "3760000000017"

    def test_rejected_with_empty_top_candidates_ok(self):
        # status='rejected' with no candidates found is legitimate (e.g.
        # parsing_status='partial' short-circuits before lookup) — empty
        # top_candidates is the correct representation.
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="rejected",
            product_ean=None,
            match_method=None,
            rejected_reason="ocr_garbage",
            top_candidates=(),
            decision_inputs=_make_decision_inputs(candidates_considered=0),
        )
        assert match.top_candidates == ()

    def test_frozen(self):
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="X",
            match_method="barcode",
            decision_inputs=_make_decision_inputs(),
        )
        with pytest.raises((ValidationError, TypeError)):
            match.status = "rejected"


class TestMatchedTicketInvariants:
    def test_store_matched_with_id_ok(self):
        sid = uuid4()
        mt = MatchedTicket(
            parsed_ticket_id=uuid4(),
            item_matches=[],
            store_match_id=sid,
            store_status="matched",
            store_rejected_reason=None,
        )
        assert mt.store_match_id == sid

    def test_store_match_id_must_be_uuid(self):
        # int is no longer accepted (bloc 2 will migrate stores PK to UUID).
        with pytest.raises(ValidationError):
            MatchedTicket(
                parsed_ticket_id=uuid4(),
                item_matches=[],
                store_match_id=42,  # type: ignore[arg-type]
                store_status="matched",
            )

    def test_store_matched_without_id_fails(self):
        with pytest.raises(ValidationError):
            MatchedTicket(
                parsed_ticket_id=uuid4(),
                item_matches=[],
                store_match_id=None,
                store_status="matched",
                store_rejected_reason=None,
            )

    def test_store_unresolved_without_reason_fails(self):
        with pytest.raises(ValidationError):
            MatchedTicket(
                parsed_ticket_id=uuid4(),
                item_matches=[],
                store_match_id=None,
                store_status="unresolved",
                store_rejected_reason=None,
            )

    def test_store_suggested_without_reason_fails(self):
        with pytest.raises(ValidationError):
            MatchedTicket(
                parsed_ticket_id=uuid4(),
                item_matches=[],
                store_match_id=None,
                store_status="suggested",
                store_rejected_reason=None,
            )

    def test_store_unresolved_with_reason_ok(self):
        mt = MatchedTicket(
            parsed_ticket_id=uuid4(),
            item_matches=[],
            store_match_id=None,
            store_status="unresolved",
            store_rejected_reason="no_candidate_within_radius",
        )
        assert mt.store_status == "unresolved"

    def test_holds_item_matches(self):
        match = ItemMatch(
            parsed_item_id=uuid4(),
            status="matched",
            product_ean="X",
            match_method="barcode",
            decision_inputs=_make_decision_inputs(),
        )
        mt = MatchedTicket(
            parsed_ticket_id=uuid4(),
            item_matches=[match],
            store_match_id=uuid4(),
            store_status="matched",
        )
        assert len(mt.item_matches) == 1

    def test_frozen(self):
        mt = MatchedTicket(
            parsed_ticket_id=uuid4(),
            item_matches=[],
            store_match_id=uuid4(),
            store_status="matched",
        )
        with pytest.raises((ValidationError, TypeError)):
            mt.store_match_id = uuid4()


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


class TestImageHash:
    def test_reproducible(self):
        h1 = compute_image_hash(b"hello world")
        h2 = compute_image_hash(b"hello world")
        assert h1 == h2
        assert len(h1) == 64

    def test_sensitive_to_one_byte(self):
        h1 = compute_image_hash(b"hello world")
        h2 = compute_image_hash(b"hello worle")
        assert h1 != h2


class TestParsedJsonbHash:
    def test_compute_directly_matches_with_jsonb_hash(self):
        receipt_id = uuid4()
        item = ParsedItem(
            raw_label="x",
            normalized_label="X",
            total_cents=100,
            source_block_ids=[],
            parsing_issues=[],
        )
        ticket = ParsedTicket(
            receipt_id=receipt_id,
            items=[item],
            header=ParsedHeader(brand="X", source_block_ids=[]),
            footer=ParsedFooter(source_block_ids=[]),
            raw_ticket_image_hash="a" * 64,
        )
        h_method = ticket.with_jsonb_hash().parsed_jsonb_hash
        h_helper = compute_parsed_jsonb_hash(ticket)
        assert h_method == h_helper
