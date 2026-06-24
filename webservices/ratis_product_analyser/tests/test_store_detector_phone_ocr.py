"""Unit tests for OCR-tolerant phone extraction in store_detector.

Country-agnostic: the regex extracts digit sequences; downstream
`normalize_phone` handles per-country format validation.

Context (alpha 2026-04-29): Intermarché receipt was OCR'd as
``EL0147459270`` (T→E + drop "L" + drop space). The strict labelled
regex ``Tel:`` did not match, the unlabelled fallback did not match
either, and the line was wrongly classified as a retailer. This test
suite locks in OCR-tolerant prefix detection for the typical variants.
"""

from __future__ import annotations

import pytest
from worker.ocr.store_detector import (
    _extract_phone_ocr_tolerant,
    extract_store_signals,
)


class TestExtractPhoneOcrTolerantHelper:
    """Pure helper: country-agnostic, returns digits-only or None."""

    @pytest.mark.parametrize(
        "line,expected",
        [
            # Canonical prefixes (FR shape, 10 digits)
            ("TEL 0147459270", "0147459270"),
            ("TEL: 0147459270", "0147459270"),
            ("TEL.0147459270", "0147459270"),
            ("Tel: 0147459270", "0147459270"),
            ("TÉL 0147459270", "0147459270"),
            ("TÉL.0147459270", "0147459270"),
            # OCR errors on prefix
            ("TE 0147459270", "0147459270"),  # drop L
            ("EL 0147459270", "0147459270"),  # T→E + drop L (real alpha bug)
            ("TEL0147459270", "0147459270"),  # drop separator
            ("EL0147459270", "0147459270"),  # combo
            ("T0147459270", "0147459270"),  # drop EL
            ("T 0147459270", "0147459270"),
            # Separator variants in digits
            ("Tel: 01.47.45.92.70", "0147459270"),
            ("Tel: 01 47 45 92 70", "0147459270"),
            ("Tel: 01-47-45-92-70", "0147459270"),
            # International samples — digits-only extraction (no country logic
            # at this stage; downstream normalize_phone handles validation).
            ("Tel +49 30 12345678", "493012345678"),  # DE 12 digits
            ("Phone +1 555 123 4567", "15551234567"),  # US 11 digits
        ],
    )
    def test_extracts_digits(self, line: str, expected: str) -> None:
        assert _extract_phone_ocr_tolerant(line) == expected

    @pytest.mark.parametrize(
        "line",
        [
            "BIENVENUE",  # no digits at all
            "MERCI 5",  # 1 digit, far below 8
            "12345",  # 5 digits, < 8
            "",  # empty
            "TICKET 1234567",  # 7 digits, < 8
        ],
    )
    def test_returns_none_when_too_short_or_no_phone(self, line: str) -> None:
        assert _extract_phone_ocr_tolerant(line) is None

    def test_rejects_overly_long_digit_sequence(self) -> None:
        """Store barcode (24+ digits) must NOT be classified as phone here.

        Phones cap at 15 digits per E.164. A long internal store code is
        out of phone range and must be rejected by the helper.
        """
        # 24 digits — typical receipt barcode
        assert _extract_phone_ocr_tolerant("202603270904002200207879") is None

    def test_does_not_consume_address_lines(self) -> None:
        """Address lines like '12 RUE DE L'ABREUVOIR' must not be matched."""
        assert _extract_phone_ocr_tolerant("12 RUE DE L'ABREUVOIR") is None

    def test_does_not_consume_postal_city(self) -> None:
        """'92400 COURBEVOIE' is 5 digits — below 8-digit phone floor."""
        assert _extract_phone_ocr_tolerant("92400 COURBEVOIE") is None


class TestPhoneOcrIntegration:
    """End-to-end: extract_store_signals must emit phone for OCR variants."""

    @pytest.mark.parametrize(
        "phone_line",
        [
            "TEL 0147459270",
            "TE 0147459270",
            "EL 0147459270",
            "TEL0147459270",
            "EL0147459270",  # the real alpha 2026-04-29 bug
            "T0147459270",
            "TÉL.0147459270",
            "TÉL 0147459270",
        ],
    )
    def test_extracts_phone_for_ocr_variants(self, phone_line: str) -> None:
        lines = ["INTERMARCHE", "12 RUE DE LA PAIX", "75001 PARIS", phone_line]
        signals = extract_store_signals(lines)
        assert signals.get("phone") == "0147459270", f"Expected phone extracted from {phone_line!r}, got {signals=}"

    def test_ocr_variant_does_not_pollute_retailer_field(self) -> None:
        """Before fix: 'EL0147459270' fell through to the retailer regex
        (uppercase + digits) and was wrongly captured as retailer.

        After fix: phone is extracted in priority-1 path, retailer remains
        the real header value.
        """
        lines = ["INTERMARCHE", "EL0147459270"]
        signals = extract_store_signals(lines)
        assert signals.get("phone") == "0147459270"
        assert signals.get("retailer") == "INTERMARCHE"

    def test_canonical_labelled_phone_still_works(self) -> None:
        """Backward compat: clean Tel: format must keep working."""
        lines = ["MONOPRIX", "Tel: 0149970970"]
        signals = extract_store_signals(lines)
        assert signals.get("phone") == "0149970970"

    def test_invalid_phone_digits_not_emitted(self) -> None:
        """If digits don't validate as FR phone, normalize_phone returns
        None and we must NOT emit a phone signal.
        """
        # 9 digits — too short for FR (which expects 10 digits 0[1-9]\d{8})
        lines = ["BOULANGERIE", "TEL 123456789"]
        signals = extract_store_signals(lines)
        assert "phone" not in signals
