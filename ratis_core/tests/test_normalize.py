"""Tests for ratis_core.normalize — pure functions, no DB needed."""

from ratis_core.normalize import normalize_numeric, normalize_phone


class TestNormalizeNumeric:
    def test_identity_for_clean_digits(self):
        assert normalize_numeric("1234567890") == "1234567890"

    def test_strips_spaces(self):
        assert normalize_numeric("12 34") == "1234"

    def test_strips_dashes_dots_slashes(self):
        assert normalize_numeric("12-34.56/78") == "12345678"

    def test_ocr_letter_O_to_zero(self):
        assert normalize_numeric("O1A9B7") == "014987"

    def test_all_ocr_fixes(self):
        # O→0, I→1, Z→2, A→4, S→5, G→6, g→6, B→8, l→1, o→0
        assert normalize_numeric("OIZASGgBlo") == "0124566810"

    def test_non_ocr_letters_preserved(self):
        # Letters not in the confusion map are left as-is
        assert normalize_numeric("X1Y2") == "X1Y2"

    def test_empty_string(self):
        assert normalize_numeric("") == ""


class TestNormalizePhoneFR:
    def test_clean_10_digit(self):
        assert normalize_phone("0149970970") == "0149970970"

    def test_spaces_between_pairs(self):
        # Intermarché format: "01 49 97 09 70"
        assert normalize_phone("01 49 97 09 70") == "0149970970"

    def test_dots_between_pairs(self):
        assert normalize_phone("01.49.97.09.70") == "0149970970"

    def test_dashes_between_pairs(self):
        assert normalize_phone("01-49-97-09-70") == "0149970970"

    def test_ocr_errors_in_phone(self):
        # O→0, A→4, I→1 — all in one string
        assert normalize_phone("O1 A9 97 O9 7O") == "0149970970"

    def test_ocr_errors_in_0033_prefix(self):
        # OCR commonly misreads leading zeros as 'O'
        assert normalize_phone("OO33 1 49 97 09 70") == "0149970970"

    def test_plus33_prefix(self):
        assert normalize_phone("+33 1 49 97 09 70") == "0149970970"

    def test_0033_prefix(self):
        assert normalize_phone("0033 1 49 97 09 70") == "0149970970"

    def test_returns_none_for_invalid(self):
        assert normalize_phone("NOTAPHONE") is None

    def test_returns_none_for_too_short(self):
        assert normalize_phone("012345") is None

    def test_returns_none_for_empty(self):
        assert normalize_phone("") is None

    def test_country_code_default_is_fr(self):
        assert normalize_phone("0149970970") == normalize_phone("0149970970", country_code="FR")

    def test_unknown_country_code_returns_none(self):
        # V1: any country_code other than FR returns None (not implemented)
        assert normalize_phone("0149970970", country_code="DE") is None
