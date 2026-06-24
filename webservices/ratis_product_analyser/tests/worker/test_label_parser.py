"""Unit tests for ``worker.ocr.label_parser.parse_label``.

Bloc E (NRC) — focuses on the OCR-text EAN-13 recovery path :
when ``pyzbar`` upstream (in ``label_task``) misses the barcode, the parser
must fall back to scanning the OCR result for ``\\d{13}`` patterns and only
keep candidates that pass the GS1 checksum.

These tests exercise ``parse_label`` directly. The pyzbar→OCR fallback
ordering is handled by ``label_task`` (Bloc D — already shipped) and is
covered by ``test_label_task.py`` ; here we only verify the OCR-side
recovery behavior in isolation.
"""

from __future__ import annotations

from decimal import Decimal

from worker.ocr.label_parser import parse_label
from worker.ocr.types import OcrResult


def _r(*lines: str, conf: float = 0.95) -> OcrResult:
    return [(line, conf) for line in lines]


# Reference EANs (real-world, both with valid GS1 checksum) :
#   3017620422003 — Nutella 400g (cited in ARCH § Bloc E)
#   7610113013175 — Hipro yogurt
NUTELLA = "3017620422003"
HIPRO = "7610113013175"
NUTELLA_BAD = "3017620422004"  # last digit +1 → invalid checksum
HIPRO_BAD = "7610113013176"


class TestEanFromOcrChecksum:
    """Bloc E — OCR-extracted EAN must pass EAN-13 checksum validation."""

    def test_valid_ean_13_in_ocr_kept(self):
        """OCR text contains one valid-checksum EAN-13 → product_ean filled."""
        result = parse_label(
            _r(
                "NUTELLA 400G",
                NUTELLA,
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean == NUTELLA

    def test_invalid_checksum_ean_13_dropped(self):
        """OCR text contains a 13-digit number but checksum invalid → product_ean=None."""
        result = parse_label(
            _r(
                "NUTELLA 400G",
                NUTELLA_BAD,
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean is None
        # The (invalid) 13-digit number must NOT contaminate the price field
        # — it stays ignored as a noise block.
        assert result.price == Decimal("2.50")

    def test_two_candidates_one_valid_picks_valid(self):
        """OCR has 2× \\d{13} blocks ; only one valid → keep the valid one."""
        result = parse_label(
            _r(
                "NUTELLA 400G",
                NUTELLA_BAD,  # invalid checksum — must be rejected
                NUTELLA,  # valid — must be picked
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean == NUTELLA

    def test_two_valid_candidates_unresolved(self):
        """V1 strict — 2 distinct valid EAN-13 in OCR → no tie-break, ean=None.

        Recovery via Levenshtein/similarity is V2 (batch reconciliation, Bloc E.2).
        """
        result = parse_label(
            _r(
                "PRODUIT MYSTERE",
                NUTELLA,
                HIPRO,
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean is None

    def test_same_valid_ean_repeated_kept(self):
        """Same valid EAN appears twice (e.g. printed below + above barcode) →
        deduplicate ; counts as a single candidate, kept."""
        result = parse_label(
            _r(
                "NUTELLA 400G",
                NUTELLA,
                NUTELLA,
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean == NUTELLA

    def test_no_ean_pattern_in_ocr(self):
        """No \\d{13} block in OCR → product_ean=None, parser still returns
        the price+name pair (best-effort downgrade)."""
        result = parse_label(
            _r(
                "NUTELLA 400G",
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean is None
        assert result.price == Decimal("2.50")
        assert result.scanned_name == "NUTELLA 400G"


class TestEan8FallthroughUnchanged:
    """Bloc E gotcha — EAN-8 keeps the legacy first-match-wins behavior
    (no checksum V1 — rare in France, deferred to V2)."""

    def test_valid_ean_8_format_kept(self):
        """A pure EAN-8 (8 digits) is still extracted as before."""
        result = parse_label(
            _r(
                "MINI PRODUIT",
                "20012345",  # 8-digit
                "0,99",
            )
        )
        assert result is not None
        assert result.product_ean == "20012345"


class TestPriceAndNamePreserved:
    """Bloc E must NOT regress the existing price/name extraction logic."""

    def test_inline_ean_and_price_block(self):
        """EAN on its own block, price on a separate block — both extracted."""
        result = parse_label(
            _r(
                "BEURRE DOUX 250G",
                NUTELLA,
                "1,89",
            )
        )
        assert result is not None
        assert result.scanned_name == "BEURRE DOUX 250G"
        assert result.price == Decimal("1.89")
        assert result.product_ean == NUTELLA

    def test_returns_none_when_no_price(self):
        """No price → parser returns None (matches pre-Bloc-E behavior)."""
        result = parse_label(
            _r(
                "PRODUIT SANS PRIX",
                NUTELLA,
            )
        )
        assert result is None

    def test_ean_block_with_trailing_text_strips_ean_then_parses_rest(self):
        """If the EAN block also contains text (e.g. ``'EAN: 3017620422003 BIO'``)
        the EAN is stripped and the remainder is processed as a name candidate."""
        result = parse_label(
            _r(
                "NUTELLA",
                f"EAN: {NUTELLA} BIO",
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean == NUTELLA
        # First name candidate wins
        assert result.scanned_name == "NUTELLA"


class TestThirteenDigitNoiseRejected:
    """Bloc E hardening — 13-digit numbers that LOOK like an EAN but aren't
    (date concat, store ID, etc.) are filtered out by checksum and don't
    masquerade as a product match."""

    def test_pure_noise_13_digits_rejected(self):
        """Random-looking 13-digit string with bad checksum → product_ean=None.

        Pre-Bloc-E this would have been falsely accepted as an EAN.
        """
        result = parse_label(
            _r(
                "PRODUIT X",
                "1234567890123",  # checksum invalid
                "2,50",
            )
        )
        assert result is not None
        assert result.product_ean is None

    def test_date_like_13_digits_with_bad_checksum_rejected(self):
        """``2026050214300`` (looks like a date+time concat) — bad checksum."""
        # Verify the chosen literal indeed has a bad checksum so the test
        # actually exercises the rejection path :
        from ratis_core.utils.ean_checksum import validate_ean13_checksum

        candidate = "2026050214300"
        assert not validate_ean13_checksum(candidate)

        result = parse_label(
            _r(
                "PRODUIT Y",
                candidate,
                "3,99",
            )
        )
        assert result is not None
        assert result.product_ean is None
