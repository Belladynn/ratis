"""Unit tests for the pipeline receipt barcode wrapper.

Covers :

- :class:`ParsedReceiptBarcode` invariants (frozen, raw-only minimal,
  optional ``extra``).
- ``_parse_date_field`` length-inference (8 → ``YYYYMMDD``, 6 → ``YYMMDD``)
  and rejection paths (wrong length, non-digit, invalid calendar date,
  ``None``).
- ``_parse_time_field`` length-inference (4 → ``HHMM``, 6 → ``HHMMSS``)
  and rejection paths.
- ``_extract_extra`` : excludes canonical fields, returns ``None`` when
  empty, stringifies values.
- ``parse_receipt_barcode`` end-to-end with a stubbed
  ``load_barcode_formats`` (no DB).

Pure unit-tests : no DB connection used. The ``db`` arg is a
``MagicMock(Session)`` ; ``load_barcode_formats`` is monkeypatched on
the module under test to return a canned format dict.
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from worker.pipeline import barcode as barcode_mod
from worker.pipeline.barcode import (
    _extract_extra,
    _parse_date_field,
    _parse_time_field,
    parse_receipt_barcode,
)
from worker.pipeline.types import ParsedReceiptBarcode

# ---------------------------------------------------------------------------
# ParsedReceiptBarcode invariants
# ---------------------------------------------------------------------------


class TestParsedReceiptBarcode:
    def test_raw_only_minimal(self):
        pr = ParsedReceiptBarcode(raw="123456789012345")
        assert pr.raw == "123456789012345"
        assert pr.retailer_key is None
        assert pr.store_code is None
        assert pr.caisse is None
        assert pr.tx_id is None
        assert pr.date is None
        assert pr.time is None
        assert pr.extra is None

    def test_frozen_immutable(self):
        pr = ParsedReceiptBarcode(raw="abc")
        with pytest.raises((ValidationError, TypeError)):
            pr.raw = "xyz"

    def test_extra_dict_optional(self):
        pr = ParsedReceiptBarcode(raw="123", extra={"loyalty_id": "LOY-7"})
        assert pr.extra == {"loyalty_id": "LOY-7"}

    def test_full_construction(self):
        pr = ParsedReceiptBarcode(
            raw="00012345672026043014300100201",
            retailer_key="intermarche",
            store_code="00100",
            caisse="201",
            tx_id="6712",
            date=date(2026, 4, 30),
            time=time(14, 30),
            extra={"loyalty_id": "LOY-7"},
        )
        assert pr.retailer_key == "intermarche"
        assert pr.date == date(2026, 4, 30)
        assert pr.time == time(14, 30)
        assert pr.extra == {"loyalty_id": "LOY-7"}


# ---------------------------------------------------------------------------
# _parse_date_field
# ---------------------------------------------------------------------------


class TestParseDateField:
    def test_8_digits_YYYYMMDD(self):
        assert _parse_date_field("20260430") == date(2026, 4, 30)

    def test_6_digits_YYMMDD(self):
        # Century 2000 — matches the legacy _parse_barcode_date behaviour.
        assert _parse_date_field("260430") == date(2026, 4, 30)

    def test_invalid_length_4_returns_none(self):
        assert _parse_date_field("0430") is None

    def test_invalid_length_5_returns_none(self):
        assert _parse_date_field("12345") is None

    def test_invalid_length_7_returns_none(self):
        assert _parse_date_field("2026043") is None

    def test_invalid_chars_returns_none(self):
        assert _parse_date_field("abcdefgh") is None

    def test_none_input_returns_none(self):
        assert _parse_date_field(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_date_field("") is None

    def test_invalid_calendar_feb_30_returns_none(self):
        assert _parse_date_field("20260230") is None

    def test_invalid_calendar_month_13_returns_none(self):
        assert _parse_date_field("20261330") is None


# ---------------------------------------------------------------------------
# _parse_time_field
# ---------------------------------------------------------------------------


class TestParseTimeField:
    def test_4_digits_HHMM(self):
        assert _parse_time_field("1430") == time(14, 30)

    def test_6_digits_HHMMSS(self):
        assert _parse_time_field("143045") == time(14, 30, 45)

    def test_midnight_HHMM(self):
        assert _parse_time_field("0000") == time(0, 0)

    def test_invalid_length_5_returns_none(self):
        assert _parse_time_field("14305") is None

    def test_invalid_chars_returns_none(self):
        assert _parse_time_field("abcd") is None

    def test_none_input_returns_none(self):
        assert _parse_time_field(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_time_field("") is None

    def test_invalid_calendar_25h_returns_none(self):
        assert _parse_time_field("2530") is None

    def test_invalid_calendar_60m_returns_none(self):
        assert _parse_time_field("1460") is None


# ---------------------------------------------------------------------------
# _extract_extra
# ---------------------------------------------------------------------------


class TestExtractExtra:
    def test_excludes_canonical_fields(self):
        parsed = {
            "store_code": "001",
            "caisse": "12",
            "tx_id": "999",
            "date": "20260430",
            "time": "1430",
            "loyalty_id": "LOY-7",
        }
        assert _extract_extra(parsed) == {"loyalty_id": "LOY-7"}

    def test_empty_returns_none(self):
        # Only canonical fields → no extra.
        parsed = {"store_code": "001", "date": "20260430"}
        assert _extract_extra(parsed) is None

    def test_no_fields_returns_none(self):
        assert _extract_extra({}) is None

    def test_stringifies_values(self):
        parsed = {"loyalty_int": 42}
        assert _extract_extra(parsed) == {"loyalty_int": "42"}

    def test_skips_none_values(self):
        # None values must NOT leak into extra (they would round-trip as
        # the string "None"). Only set fields surface.
        parsed = {"loyalty_id": None, "promo_code": "PROMO5"}
        assert _extract_extra(parsed) == {"promo_code": "PROMO5"}


# ---------------------------------------------------------------------------
# parse_receipt_barcode — end-to-end with stubbed format loader
# ---------------------------------------------------------------------------


_INTERMARCHE_FMT = {
    "length": 24,
    "fields": [
        {"name": "date", "start": 0, "end": 8},
        {"name": "time", "start": 8, "end": 12},
        {"name": "tx_id", "start": 12, "end": 16},
        {"name": "caisse", "start": 16, "end": 19},
        {"name": "store_code", "start": 19, "end": 24},
    ],
}

_MONOPRIX_FMT = {
    "length": 24,
    "fields": [
        {"name": "store_code", "start": 0, "end": 4},
        {"name": "caisse", "start": 4, "end": 7},
        {"name": "tx_id", "start": 7, "end": 12},
        {"name": "date", "start": 12, "end": 18},
        {"name": "time", "start": 18, "end": 24},
    ],
}

_FORMATS = {"intermarche": _INTERMARCHE_FMT, "monoprix": _MONOPRIX_FMT}


@pytest.fixture
def stub_formats(monkeypatch):
    """Monkeypatch the format loader on the module under test."""
    monkeypatch.setattr(barcode_mod, "load_barcode_formats", lambda _db: _FORMATS)
    return _FORMATS


@pytest.fixture
def db_stub() -> MagicMock:
    """A MagicMock standing in for a SQLAlchemy Session — no DB used."""
    return MagicMock(name="Session")


class TestParseReceiptBarcode:
    def test_no_retailer_returns_raw_only(self, db_stub, stub_formats):
        pr = parse_receipt_barcode("12345", None, db_stub)
        assert pr.raw == "12345"
        assert pr.retailer_key is None
        assert pr.store_code is None
        assert pr.date is None

    def test_empty_retailer_returns_raw_only(self, db_stub, stub_formats):
        pr = parse_receipt_barcode("12345", "", db_stub)
        assert pr.raw == "12345"
        assert pr.retailer_key is None

    def test_unknown_retailer_returns_raw_only(self, db_stub, stub_formats):
        # "Carrefour" not in stubbed formats ; retailer_key stays None.
        pr = parse_receipt_barcode("12345", "Carrefour", db_stub)
        assert pr.raw == "12345"
        assert pr.retailer_key is None

    def test_known_retailer_length_mismatch_returns_partial(self, db_stub, stub_formats):
        # raw length doesn't match the format's expected length (24) → legacy
        # parser returns None ; wrapper still surfaces the retailer_key.
        pr = parse_receipt_barcode("123", "Intermarché", db_stub)
        assert pr.raw == "123"
        assert pr.retailer_key == "intermarche"
        assert pr.store_code is None
        assert pr.date is None

    def test_known_retailer_intermarche_full_parse(self, db_stub, stub_formats):
        # 24-char layout : YYYYMMDD HHMM TXID CAS STORE
        # 20260430|1430|6712|201|00100
        raw = "20260430143067122010" + "0100"
        assert len(raw) == 24
        pr = parse_receipt_barcode(raw, "Intermarché", db_stub)
        assert pr.retailer_key == "intermarche"
        assert pr.store_code == "00100"
        assert pr.caisse == "201"
        assert pr.tx_id == "6712"
        assert pr.date == date(2026, 4, 30)
        assert pr.time == time(14, 30)
        assert pr.extra is None

    def test_known_retailer_monoprix_different_layout(self, db_stub, stub_formats):
        # Monoprix layout : STORE CAS TXID YYMMDD HHMMSS
        # 0042|123|45678|260430|143045
        raw = "00421234567826" + "0430143045"
        assert len(raw) == 24
        pr = parse_receipt_barcode(raw, "Monoprix", db_stub)
        assert pr.retailer_key == "monoprix"
        assert pr.store_code == "0042"
        assert pr.caisse == "123"
        assert pr.tx_id == "45678"
        assert pr.date == date(2026, 4, 30)
        assert pr.time == time(14, 30, 45)

    def test_retailer_with_accent_normalized(self, db_stub, stub_formats):
        # "Intermarché" → "intermarche" (accent stripped, lowercased) →
        # matches the format key.
        raw = "20260430143067122010" + "0100"
        pr = parse_receipt_barcode(raw, "Intermarché", db_stub)
        assert pr.retailer_key == "intermarche"

    def test_returns_frozen_instance(self, db_stub, stub_formats):
        pr = parse_receipt_barcode("12345", None, db_stub)
        with pytest.raises((ValidationError, TypeError)):
            pr.raw = "other"
