"""Unit tests for ratis_core.utils.ean_checksum.validate_ean13_checksum."""

from __future__ import annotations

from ratis_core.utils.ean_checksum import validate_ean13_checksum


class TestValidateEan13Checksum:
    # ── valid EAN-13 ──

    def test_valid_ean_13_nutella(self):
        # Nutella 400g — reference EAN cited in ARCH § Bloc E
        assert validate_ean13_checksum("3017620422003") is True

    def test_valid_ean_13_hipro(self):
        # Hipro yogurt — sample real EAN
        assert validate_ean13_checksum("7610113013175") is True

    def test_valid_ean_13_all_zeros(self):
        # Edge case : "0000000000000" has a valid checksum.
        # Accepted as-is (V1) — the consensus / product matcher is responsible
        # for rejecting non-existing EANs further down the pipeline.
        assert validate_ean13_checksum("0000000000000") is True

    # ── invalid checksum ──

    def test_invalid_checksum_one_off_nutella(self):
        # Last digit bumped by +1 — must be rejected
        assert validate_ean13_checksum("3017620422004") is False

    def test_invalid_checksum_one_off_hipro(self):
        assert validate_ean13_checksum("7610113013176") is False

    # ── invalid length ──

    def test_short_string(self):
        assert validate_ean13_checksum("12345") is False

    def test_twelve_digits(self):
        # 12 digits is EAN-12 / UPC-A territory — not EAN-13
        assert validate_ean13_checksum("301762042200") is False

    def test_long_string(self):
        assert validate_ean13_checksum("12345678901234") is False

    def test_empty_string(self):
        assert validate_ean13_checksum("") is False

    # ── invalid characters ──

    def test_non_digits(self):
        assert validate_ean13_checksum("761011301317X") is False

    def test_with_whitespace(self):
        # Caller is responsible for stripping — strict input expected
        assert validate_ean13_checksum(" 3017620422003") is False

    def test_with_dash(self):
        assert validate_ean13_checksum("3017620-422003") is False

    # ── invalid type ──

    def test_none(self):
        assert validate_ean13_checksum(None) is False  # type: ignore[arg-type]

    def test_int(self):
        assert validate_ean13_checksum(3017620422003) is False  # type: ignore[arg-type]

    def test_bytes(self):
        assert validate_ean13_checksum(b"3017620422003") is False  # type: ignore[arg-type]
