"""Tests for worker.ocr.barcode_reader — pure unit tests, pyzbar mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
from worker.ocr.barcode_reader import (
    extract_store_code,
    load_barcode_formats,
    parse_receipt_barcode,
    read_ean_barcode,
    read_receipt_barcode,
    read_receipt_barcode_with_fallbacks,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_INTERMARCHE_FORMAT = {
    "length": 24,
    "fields": [
        {"name": "date", "start": 0, "end": 8},
        {"name": "time", "start": 8, "end": 12},
        {"name": "tx_id", "start": 12, "end": 16},
        {"name": "caisse", "start": 16, "end": 19},
        {"name": "store_code", "start": 19, "end": 24},
    ],
}

_MONOPRIX_FORMAT = {
    "length": 24,
    "fields": [
        {"name": "store_code", "start": 0, "end": 4},
        {"name": "caisse", "start": 4, "end": 7},
        {"name": "tx_id", "start": 7, "end": 12},
        {"name": "date", "start": 12, "end": 18},
        {"name": "time", "start": 18, "end": 24},
    ],
}

_TEST_FORMATS = {
    "intermarche": _INTERMARCHE_FORMAT,
    "monoprix": _MONOPRIX_FORMAT,
}


def _fake_barcode(data: str) -> SimpleNamespace:
    return SimpleNamespace(data=data.encode("utf-8"), type="CODE128")


class TestReadReceiptBarcode:
    def test_reads_long_numeric_barcode(self, monkeypatch):
        barcode = "234100109106250407120518"
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode(barcode)],
        )
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert read_receipt_barcode(image) == barcode

    def test_ignores_ean13_product_barcode(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("3017620422003")],
        )
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert read_receipt_barcode(image) is None

    def test_returns_none_when_no_barcode_found(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [],
        )
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert read_receipt_barcode(image) is None

    def test_returns_none_on_pyzbar_exception(self, monkeypatch):
        def _boom(_img):
            raise RuntimeError("libzbar crashed")

        monkeypatch.setattr("worker.ocr.barcode_reader.pyzbar.decode", _boom)
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert read_receipt_barcode(image) is None

    def test_skips_non_numeric_barcode(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("https://example.com/receipt/123")],
        )
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert read_receipt_barcode(image) is None

    def test_picks_first_qualifying_barcode(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [
                _fake_barcode("3017620422003"),
                _fake_barcode("234100109106250407120518"),
            ],
        )
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert read_receipt_barcode(image) == "234100109106250407120518"


class TestParseReceiptBarcode:
    def test_parses_intermarche_format(self):
        result = parse_receipt_barcode("202603270904002200207879", "Intermarche", _TEST_FORMATS)
        assert result == {
            "date": "20260327",
            "time": "0904",
            "tx_id": "0022",
            "caisse": "002",
            "store_code": "07879",
        }

    def test_parses_intermarche_with_accent(self):
        result = parse_receipt_barcode("202603270904002200207879", "Intermarch\u00e9", _TEST_FORMATS)
        assert result is not None
        assert result["store_code"] == "07879"

    def test_parses_monoprix_format(self):
        result = parse_receipt_barcode("234100109106250407120518", "Monoprix", _TEST_FORMATS)
        assert result == {
            "store_code": "2341",
            "caisse": "001",
            "tx_id": "09106",
            "date": "250407",
            "time": "120518",
        }

    def test_parses_monoprix_uppercase_brand(self):
        result = parse_receipt_barcode("234100109106250407120518", "MONOPRIX", _TEST_FORMATS)
        assert result == {
            "store_code": "2341",
            "caisse": "001",
            "tx_id": "09106",
            "date": "250407",
            "time": "120518",
        }

    def test_parses_intermarche_uppercase_brand(self):
        result = parse_receipt_barcode("202603270904002200207879", "INTERMARCHE", _TEST_FORMATS)
        assert result is not None
        assert result["store_code"] == "07879"

    def test_returns_none_for_unknown_brand(self):
        assert parse_receipt_barcode("234100109106250407120518", "Auchan", _TEST_FORMATS) is None

    def test_returns_none_for_wrong_length(self):
        assert parse_receipt_barcode("12345678901234567890", "Monoprix", _TEST_FORMATS) is None

    def test_returns_none_for_none_brand(self):
        assert parse_receipt_barcode("234100109106250407120518", None, _TEST_FORMATS) is None

    def test_returns_none_when_formats_is_none(self):
        assert parse_receipt_barcode("234100109106250407120518", "Monoprix", None) is None

    def test_returns_none_when_formats_is_empty(self):
        assert parse_receipt_barcode("234100109106250407120518", "Monoprix", {}) is None

    def test_monoprix_second_example(self):
        result = parse_receipt_barcode("234103109975250624084122", "MONOPRIX", _TEST_FORMATS)
        assert result == {
            "store_code": "2341",
            "caisse": "031",
            "tx_id": "09975",
            "date": "250624",
            "time": "084122",
        }


class TestLoadBarcodeFormats:
    def test_returns_dict_from_db_rows(self):
        row_intermarche = SimpleNamespace(
            retailer_key="intermarche",
            length=24,
            fields=[
                {"name": "date", "start": 0, "end": 8},
                {"name": "store_code", "start": 19, "end": 24},
            ],
        )
        row_monoprix = SimpleNamespace(
            retailer_key="monoprix",
            length=24,
            fields=[
                {"name": "store_code", "start": 0, "end": 4},
            ],
        )
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            row_intermarche,
            row_monoprix,
        ]

        result = load_barcode_formats(mock_db)

        assert result == {
            "intermarche": {
                "length": 24,
                "fields": [
                    {"name": "date", "start": 0, "end": 8},
                    {"name": "store_code", "start": 19, "end": 24},
                ],
            },
            "monoprix": {
                "length": 24,
                "fields": [
                    {"name": "store_code", "start": 0, "end": 4},
                ],
            },
        }

    def test_returns_empty_dict_when_no_rows(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []

        result = load_barcode_formats(mock_db)

        assert result == {}

    def test_uses_text_sql_query(self):
        """load_barcode_formats must use text() SQL — not ORM — to avoid circular imports."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []

        load_barcode_formats(mock_db)

        call_args = mock_db.execute.call_args
        assert call_args is not None
        stmt = call_args[0][0]
        # sqlalchemy text() objects have a .text attribute
        assert hasattr(stmt, "text")
        assert "retailer_receipt_formats" in stmt.text


class TestExtractStoreCode:
    def test_extracts_from_intermarche_fields(self):
        assert (
            extract_store_code(
                {
                    "date": "20260327",
                    "time": "0904",
                    "tx_id": "0022",
                    "caisse": "002",
                    "store_code": "07879",
                }
            )
            == "07879"
        )

    def test_extracts_from_monoprix_fields(self):
        assert (
            extract_store_code(
                {
                    "store_code": "2341",
                    "caisse": "001",
                    "tx_id": "09106",
                    "date": "250407",
                    "time": "120518",
                }
            )
            == "2341"
        )

    def test_returns_none_for_none_fields(self):
        assert extract_store_code(None) is None

    def test_returns_none_when_no_store_code_key(self):
        assert extract_store_code({"date": "250407", "tx_id": "09106"}) is None


class TestReadReceiptBarcodeWithFallbacks:
    """Multi-pass barcode reading — lazy generator, stops on first hit."""

    _BARCODE = "234100109106250407120518"

    def _image(self) -> np.ndarray:
        return np.zeros((10, 10, 3), dtype=np.uint8)

    def _identity(self, img: np.ndarray) -> np.ndarray:
        return img

    def test_returns_barcode_found_on_original(self, monkeypatch):
        """Found on original image — no preprocessing pass is called."""
        corrected_called = []
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode(self._BARCODE)],
        )
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pass_corrected",
            lambda img: corrected_called.append(1) or img,
        )
        result = read_receipt_barcode_with_fallbacks(self._image())
        assert result == self._BARCODE
        assert corrected_called == []  # lazy — preprocessing skipped entirely

    def test_falls_back_to_corrected_pass(self, monkeypatch):
        """Original fails, corrected pass succeeds — exactly 2 pyzbar calls."""
        call_count = [0]

        def _decode(img):
            call_count[0] += 1
            return [_fake_barcode(self._BARCODE)] if call_count[0] >= 2 else []

        monkeypatch.setattr("worker.ocr.barcode_reader.pyzbar.decode", _decode)
        monkeypatch.setattr("worker.ocr.barcode_reader.pass_corrected", self._identity)
        assert read_receipt_barcode_with_fallbacks(self._image()) == self._BARCODE
        assert call_count[0] == 2

    def test_falls_back_through_clahe(self, monkeypatch):
        """Original + corrected fail; CLAHE succeeds — exactly 3 pyzbar calls."""
        call_count = [0]

        def _decode(img):
            call_count[0] += 1
            return [_fake_barcode(self._BARCODE)] if call_count[0] >= 3 else []

        monkeypatch.setattr("worker.ocr.barcode_reader.pyzbar.decode", _decode)
        for fn in ("pass_corrected", "pass_clahe"):
            monkeypatch.setattr(f"worker.ocr.barcode_reader.{fn}", self._identity)
        assert read_receipt_barcode_with_fallbacks(self._image()) == self._BARCODE
        assert call_count[0] == 3

    def test_inverted_pass_is_last_resort(self, monkeypatch):
        """All 4 earlier passes fail; inverted succeeds — exactly 5 pyzbar calls."""
        call_count = [0]

        def _decode(img):
            call_count[0] += 1
            return [_fake_barcode(self._BARCODE)] if call_count[0] >= 5 else []

        monkeypatch.setattr("worker.ocr.barcode_reader.pyzbar.decode", _decode)
        for fn in ("pass_corrected", "pass_clahe", "pass_binarized", "pass_inverted"):
            monkeypatch.setattr(f"worker.ocr.barcode_reader.{fn}", self._identity)
        result = read_receipt_barcode_with_fallbacks(self._image())
        assert result == self._BARCODE
        assert call_count[0] == 5

    def test_returns_none_when_all_passes_fail(self, monkeypatch):
        """All 5 passes yield nothing — returns None."""
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [],
        )
        for fn in ("pass_corrected", "pass_clahe", "pass_binarized", "pass_inverted"):
            monkeypatch.setattr(f"worker.ocr.barcode_reader.{fn}", self._identity)
        assert read_receipt_barcode_with_fallbacks(self._image()) is None

    def test_corrected_is_computed_once_shared_by_subsequent_passes(self, monkeypatch):
        """pass_corrected must be called exactly once even when multiple passes are tried."""
        corrected_calls = []
        call_count = [0]

        def _corrected(img):
            corrected_calls.append(1)
            return img

        def _decode(img):
            call_count[0] += 1
            return [_fake_barcode(self._BARCODE)] if call_count[0] >= 4 else []

        monkeypatch.setattr("worker.ocr.barcode_reader.pyzbar.decode", _decode)
        monkeypatch.setattr("worker.ocr.barcode_reader.pass_corrected", _corrected)
        monkeypatch.setattr("worker.ocr.barcode_reader.pass_clahe", self._identity)
        monkeypatch.setattr("worker.ocr.barcode_reader.pass_binarized", self._identity)
        read_receipt_barcode_with_fallbacks(self._image())
        assert len(corrected_calls) == 1  # computed once, reused by clahe + binarized


class TestReadEanBarcode:
    """Unit tests for read_ean_barcode — pyzbar mocked."""

    def test_reads_ean13(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("3017620422003")],
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) == "3017620422003"

    def test_reads_ean8(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("12345678")],
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) == "12345678"

    def test_ignores_long_receipt_barcode(self, monkeypatch):
        """Receipt barcodes (≥20 digits) must NOT be returned as EAN."""
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("234103107180250410111252")],
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) is None

    def test_ignores_non_numeric(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("ABCDEFGHIJKLM")],
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) is None

    def test_returns_none_when_no_barcode(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [],
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) is None

    def test_picks_ean13_over_short_code(self, monkeypatch):
        """When multiple barcodes found, pick the EAN13/EAN8, not a random short code."""
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: [_fake_barcode("12345"), _fake_barcode("3017620422003")],
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) == "3017620422003"

    def test_returns_none_on_pyzbar_exception(self, monkeypatch):
        monkeypatch.setattr(
            "worker.ocr.barcode_reader.pyzbar.decode",
            lambda _img: (_ for _ in ()).throw(RuntimeError("zbar crash")),
        )
        assert read_ean_barcode(np.zeros((10, 10, 3), dtype=np.uint8)) is None
