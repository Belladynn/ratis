"""Unit tests for the token-level word-similarity helpers.

These helpers (Levenshtein primitive, edit-distance gate,
``_best_matching_word``) survive the matcher consensus-only refonte
(2026-05-02) — they are used by ``_normalize_text`` to clean up OCR
tokens against the lexical dictionary (``products.product_name_fr``
∪ ``products.name``).

The previously-tested ``_fuzzy_match`` ambiguity detection is gone
along with whole-product fuzzy matching ; tests for that path were
dropped in this PR (per refonte brief).
"""

from __future__ import annotations

from worker.ocr.normalize import (
    _best_matching_word,
    _levenshtein,
    _word_edit_distance_acceptable,
)

# ── A. Levenshtein primitive ──────────────────────────────────────────────────


class TestLevenshtein:
    def test_identical_strings_distance_zero(self):
        assert _levenshtein("HIPRO", "HIPRO") == 0

    def test_one_insertion(self):
        assert _levenshtein("HIPRO", "HIPROA") == 1

    def test_one_deletion(self):
        assert _levenshtein("HIPROA", "HIPRO") == 1

    def test_one_substitution(self):
        assert _levenshtein("HIPRO", "HIPRA") == 1

    def test_empty_string(self):
        assert _levenshtein("", "HIPRO") == 5
        assert _levenshtein("HIPRO", "") == 5

    def test_many_diffs(self):
        assert _levenshtein("HIPRO", "HIPOPOTAME") >= 5

    def test_two_substitutions(self):
        # PASTA -> PESTO: 2 substitutions
        assert _levenshtein("PASTA", "PESTO") == 2


# ── A. word edit distance gate ────────────────────────────────────────────────


class TestWordEditDistanceAcceptable:
    def test_identical_accepted(self):
        assert _word_edit_distance_acceptable("HIPRO", "HIPRO")

    def test_one_char_diff_accepted(self):
        # OCR-typo style: one extra char
        assert _word_edit_distance_acceptable("HIPROA", "HIPRO")
        assert _word_edit_distance_acceptable("HIPRO", "HIPROA")

    def test_two_char_diff_accepted(self):
        # MOUSSE vs MOUSSEUX: len_diff=2, lev=2 (insert U + insert X)
        assert _word_edit_distance_acceptable("MOUSSE", "MOUSSEUX")

    def test_quick_reject_long_len_diff(self):
        # MOUSSE vs MOUSSEUSEMENT: len_diff=7 -> immediate reject
        assert not _word_edit_distance_acceptable("MOUSSE", "MOUSSEUSEMENT")

    def test_long_token_unrelated_word_rejected(self):
        # HIPRO vs HIPOPOTAME: len_diff=5 -> reject
        assert not _word_edit_distance_acceptable("HIPRO", "HIPOPOTAME")

    def test_substitution_within_threshold(self):
        # PASTA vs PASTE: 1 sub -> accept
        assert _word_edit_distance_acceptable("PASTA", "PASTE")

    def test_case_insensitive(self):
        assert _word_edit_distance_acceptable("hipro", "HIPRO")
        assert _word_edit_distance_acceptable("HipRo", "hipro")

    def test_custom_max_diff_zero_strict(self):
        # max_diff=0 means must be identical
        assert _word_edit_distance_acceptable("HIPRO", "HIPRO", max_diff=0)
        assert not _word_edit_distance_acceptable("HIPRO", "HIPROA", max_diff=0)


# ── A+B. _best_matching_word with hard gate ────────────────────────────────────


class TestBestMatchingWordWithHardGate:
    def test_token_present_in_product_name(self):
        assert _best_matching_word("HIPRO", "Hipro a boire fraise") == "Hipro"

    def test_ocr_typo_one_char_matches(self):
        # HIPROA (lev=1 vs Hipro) passes both gate and ratio
        assert _best_matching_word("HIPROA", "Hipro a boire fraise") == "Hipro"

    def test_long_difference_rejected_by_gate(self):
        # MOUSSE vs Mousseusement: len_diff=7 -> rejected by edit-distance gate
        # before any SequenceMatcher computation
        assert _best_matching_word("MOUSSE", "Mousseusement") is None

    def test_unrelated_word_rejected(self):
        # HIPRO vs Hippopotame: len_diff=5 -> rejected by gate
        assert _best_matching_word("HIPRO", "Hippopotame en peluche") is None

    def test_picks_closest_among_many(self):
        result = _best_matching_word("LACTEL", "Lait demi ecreme 1L Lactel")
        assert result == "Lactel"

    def test_low_ratio_returns_none(self):
        # ZZZZZZ vs every word: gate rejects (len_diff or sub) -> None
        assert _best_matching_word("ZZZZZZ", "Nutella 400g") is None
