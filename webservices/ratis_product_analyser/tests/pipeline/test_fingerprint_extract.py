"""Unit tests for :mod:`worker.pipeline.fingerprint_extract`.

Each test builds a minimal ParsedTicket + MatchedTicket and asserts the
projection lands on the 10 expected ``FingerprintComponents`` fields.

No DB, no IO — pure function tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from datetime import time as time_cls

import pytest
from worker.pipeline.fingerprint_extract import (
    extract_components_from_pipeline_output,
)
from worker.pipeline.types import (
    DecisionInputs,
    ItemMatch,
    MatchedTicket,
    ParsedFooter,
    ParsedHeader,
    ParsedItem,
    ParsedReceiptBarcode,
    ParsedTicket,
    VatLine,
)

CAPTURED_AT = datetime(2026, 4, 30, 14, 30, 45, tzinfo=UTC)


# ── Builders ──────────────────────────────────────────────────────────────


def _build_parsed(
    *,
    brand: str | None = "INTERMARCHE",
    address_line: str | None = "1 RUE DE PARIS",
    postcode: str | None = "92400",
    city: str | None = "COURBEVOIE",
    total_cents: int | None = 1234,
    item_count_declared: int | None = 5,
    payment_method: str | None = "CB",
    vat_breakdown: tuple = (),
    barcode: ParsedReceiptBarcode | None = None,
    purchased_at: datetime | None = CAPTURED_AT,
) -> ParsedTicket:
    receipt_id = uuid.uuid4()
    header = ParsedHeader(
        brand=brand,
        address_line=address_line,
        postcode=postcode,
        city=city,
        phone=None,
        siret=None,
        source_block_ids=(),
    )
    footer = ParsedFooter(
        total_cents=total_cents,
        vat_breakdown=vat_breakdown,
        payment_method=payment_method,
        item_count_declared=item_count_declared,
        barcode=barcode,
        source_block_ids=(),
    )
    item = ParsedItem(
        raw_label="BANANE",
        normalized_label="BANANE",
        quantity=1,
        unit_price_cents=199,
        total_cents=199,
        source_block_ids=(),
        parsing_issues=(),
    )
    return ParsedTicket(
        receipt_id=receipt_id,
        items=(item,),
        header=header,
        footer=footer,
        purchased_at=purchased_at,
        raw_ticket_image_hash="a" * 64,
    ).with_jsonb_hash()


def _matched(
    parsed: ParsedTicket,
    *,
    store_match_id: uuid.UUID | None = None,
    store_status: str = "unresolved",
) -> MatchedTicket:
    item = parsed.items[0]
    return MatchedTicket(
        parsed_ticket_id=parsed.id,
        item_matches=(
            ItemMatch(
                parsed_item_id=item.id,
                status="unresolved",
                rejected_reason="no_candidate",
                decision_inputs=DecisionInputs(
                    normalized_label=item.normalized_label,
                    barcode_used=None,
                    knowledge_lookup_hit=False,
                    consensus_state=None,
                    candidates_considered=0,
                ),
            ),
        ),
        store_match_id=store_match_id,
        store_status=store_status,
        store_rejected_reason=(None if store_status == "matched" else "no_candidate"),
    )


# ── Field-by-field mapping ────────────────────────────────────────────────


class TestStoreIdMapping:
    def test_store_matched_yields_uuid_string(self):
        parsed = _build_parsed()
        sid = uuid.uuid4()
        matched = _matched(parsed, store_match_id=sid, store_status="matched")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=matched)
        assert c.store_id == str(sid)

    def test_store_unresolved_yields_none(self):
        parsed = _build_parsed()
        matched = _matched(parsed, store_status="unresolved")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=matched)
        assert c.store_id is None

    def test_store_suggested_yields_none(self):
        """``suggested`` means the matcher proposed a candidate but didn't
        confirm — fingerprint must not commit to a non-confirmed id."""
        parsed = _build_parsed()
        matched = _matched(parsed, store_status="suggested")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=matched)
        assert c.store_id is None


class TestAddressNormalized:
    def test_address_line_present_used_as_is_uppercased(self):
        parsed = _build_parsed(address_line="1 rue de paris")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.address_normalized == "1 RUE DE PARIS"

    def test_address_line_missing_falls_back_to_postcode_city(self):
        parsed = _build_parsed(address_line=None, postcode="92400", city="Courbevoie")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.address_normalized == "92400 COURBEVOIE"

    def test_address_all_missing_yields_none(self):
        parsed = _build_parsed(address_line=None, postcode=None, city=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.address_normalized is None

    def test_address_line_only_postcode_set(self):
        parsed = _build_parsed(address_line=None, postcode="92400", city=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.address_normalized == "92400"


class TestBrandNormalized:
    def test_brand_present(self):
        parsed = _build_parsed(brand="Intermarché")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.brand_normalized == "INTERMARCHÉ"  # no accent fold here

    def test_brand_missing(self):
        parsed = _build_parsed(brand=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.brand_normalized is None

    def test_brand_blank_string(self):
        parsed = _build_parsed(brand="   ")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.brand_normalized is None


class TestIsoDate:
    def test_purchased_at_present(self):
        parsed = _build_parsed(purchased_at=CAPTURED_AT)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.iso_date == "2026-04-30"

    def test_purchased_at_missing(self):
        parsed = _build_parsed(purchased_at=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.iso_date is None


class TestIsoTimeAndPrecision:
    def test_barcode_time_present_yields_second_precision(self):
        bc = ParsedReceiptBarcode(
            raw="20260430143067",
            time=time_cls(14, 30, 45),
        )
        parsed = _build_parsed(barcode=bc)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.iso_time == "14:30:45"
        assert c.time_precision == "second"

    def test_barcode_time_with_zero_seconds_still_second_precision(self):
        """Zero seconds are valid — barcode encodes HHMMSS always."""
        bc = ParsedReceiptBarcode(
            raw="20260430143000",
            time=time_cls(14, 30, 0),
        )
        parsed = _build_parsed(barcode=bc)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.iso_time == "14:30:00"
        assert c.time_precision == "second"

    def test_no_barcode_time_yields_none(self):
        parsed = _build_parsed(barcode=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.iso_time is None
        assert c.time_precision is None

    def test_barcode_present_but_no_time_field(self):
        bc = ParsedReceiptBarcode(raw="some-raw-barcode-value", time=None)
        parsed = _build_parsed(barcode=bc)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.iso_time is None
        assert c.time_precision is None


class TestTotalAndItemCount:
    def test_total_propagated(self):
        parsed = _build_parsed(total_cents=4250)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.total_ttc_cents == 4250

    def test_total_zero_distinct_from_none(self):
        parsed_zero = _build_parsed(total_cents=0)
        parsed_none = _build_parsed(total_cents=None)
        c_zero = extract_components_from_pipeline_output(parsed=parsed_zero, matched=_matched(parsed_zero))
        c_none = extract_components_from_pipeline_output(parsed=parsed_none, matched=_matched(parsed_none))
        assert c_zero.total_ttc_cents == 0
        assert c_none.total_ttc_cents is None

    def test_item_count_propagated(self):
        parsed = _build_parsed(item_count_declared=12)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.item_count_declared == 12

    def test_item_count_missing(self):
        parsed = _build_parsed(item_count_declared=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.item_count_declared is None


class TestPaymentMethodNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("CB", "cb"),
            ("cb", "cb"),
            ("Carte Bancaire", "cb"),
            ("CARTE BLEUE", "cb"),
            ("CREDIT", "cb"),
            ("PAIEMENT CB", "cb"),
            ("Espèces", "cash"),
            ("ESPECES", "cash"),
            ("Cash", "cash"),
            ("LIQUIDE", "cash"),
            ("Chèque", "check"),
            ("CHEQUE", "check"),
            ("CHECK", "check"),
            ("PAR CHEQUE", "check"),
            ("TICKET RESTO", "other"),
            ("AMEX", "other"),
            ("MAESTRO", "other"),
        ],
    )
    def test_normalization(self, raw, expected):
        parsed = _build_parsed(payment_method=raw)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.payment_method == expected

    def test_payment_method_none(self):
        parsed = _build_parsed(payment_method=None)
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.payment_method is None

    def test_payment_method_blank(self):
        parsed = _build_parsed(payment_method="   ")
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.payment_method is None


class TestTvaTotalCents:
    def test_single_vat_line(self):
        parsed = _build_parsed(
            vat_breakdown=(
                VatLine(
                    rate_pct=20.0,
                    taxable_cents=1000,
                    tax_cents=200,
                    source_block_ids=(),
                ),
            )
        )
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.tva_total_cents == 200

    def test_multiple_vat_lines_sum(self):
        parsed = _build_parsed(
            vat_breakdown=(
                VatLine(rate_pct=5.5, taxable_cents=500, tax_cents=28, source_block_ids=()),
                VatLine(rate_pct=10.0, taxable_cents=300, tax_cents=30, source_block_ids=()),
                VatLine(rate_pct=20.0, taxable_cents=1000, tax_cents=200, source_block_ids=()),
            )
        )
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.tva_total_cents == 28 + 30 + 200

    def test_empty_vat_breakdown_yields_none(self):
        parsed = _build_parsed(vat_breakdown=())
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.tva_total_cents is None

    def test_vat_lines_with_zero_tax_yield_zero_not_none(self):
        """Receipts with a printed-but-zero VAT line (e.g. tax-exempt
        flagship purchases) must yield ``0`` rather than ``None``."""
        parsed = _build_parsed(
            vat_breakdown=(VatLine(rate_pct=0.0, taxable_cents=100, tax_cents=0, source_block_ids=()),)
        )
        c = extract_components_from_pipeline_output(parsed=parsed, matched=_matched(parsed))
        assert c.tva_total_cents == 0


# ── Determinism ───────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_output(self):
        parsed = _build_parsed(
            vat_breakdown=(VatLine(rate_pct=20.0, taxable_cents=1000, tax_cents=200, source_block_ids=()),),
            barcode=ParsedReceiptBarcode(raw="x" * 20, time=time_cls(14, 30, 45), date=date(2026, 4, 30)),
        )
        matched = _matched(parsed, store_match_id=uuid.uuid4(), store_status="matched")
        c1 = extract_components_from_pipeline_output(parsed=parsed, matched=matched)
        c2 = extract_components_from_pipeline_output(parsed=parsed, matched=matched)
        assert c1 == c2
