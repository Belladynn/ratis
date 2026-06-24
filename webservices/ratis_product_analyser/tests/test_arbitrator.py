from __future__ import annotations

from difflib import SequenceMatcher

from worker.ocr.arbitrator import _FUZZY_CONSENSUS_THRESHOLD, arbitrate
from worker.ocr.types import OcrResult


def _blocks(text: str, conf: float = 0.95) -> OcrResult:
    """Helper: single block result."""
    return [(text, conf)]


def _multi(*texts: str) -> OcrResult:
    return [(t, 0.95) for t in texts]


class TestArbitrate:
    def test_three_identical_returns_result(self):
        p = _multi("NUTELLA 2.50", "LAIT 0.99")
        result = arbitrate(p, p, p)
        assert result == p

    def test_two_identical_plus_one_different_returns_majority(self):
        p1 = _blocks("NUTELLA 2.50 LAIT 0.99")
        p2 = _blocks("NUTELLA 2.50 LAIT 0.99")
        p3 = _blocks("NUTELLA 2,50 LAiT 0,99")  # slight difference
        result = arbitrate(p1, p2, p3)
        assert result == p1

    def test_all_three_different_returns_none(self):
        result = arbitrate(
            _blocks("NUTELLA 2.50"),
            _blocks("TOTAL 5.99"),
            _blocks("JAMBON 3.49"),
        )
        assert result is None

    def test_empty_passes_return_none(self):
        assert arbitrate([], [], []) is None

    def test_clahe_binarized_agree_returns_clahe(self):
        p1 = _blocks("GARBAGE")
        p2 = _blocks("NUTELLA 2.50")
        p3 = _blocks("NUTELLA 2.50")
        result = arbitrate(p1, p2, p3)
        assert result == p2

    def test_convergence_is_case_and_space_insensitive(self):
        """Two passes with different casing/spacing are still considered identical."""
        p1 = _blocks("nutella  2.50")
        p2 = _blocks("NUTELLA 2.50")
        p3 = _blocks("GARBAGE")
        result = arbitrate(p1, p2, p3)
        assert result == p1


class TestFuzzyConsensus:
    """
    When all 3 passes differ but are mutually similar (OCR character errors),
    arbitrate should return the result with the highest cumulative confidence.
    """

    def test_typical_ocr_errors_returns_highest_confidence(self):
        """NUTELLA / NUT3LLA / NUTELL4 — all fuzzy-similar → pick highest conf."""
        p1 = _blocks("NUTELLA", conf=0.92)
        p2 = _blocks("NUT3LLA", conf=0.85)
        p3 = _blocks("NUTELL4", conf=0.78)
        result = arbitrate(p1, p2, p3)
        assert result == p1

    def test_picks_highest_confidence_regardless_of_order(self):
        """Confidence winner in p3 position."""
        p1 = _blocks("NUT3LLA", conf=0.78)
        p2 = _blocks("NUTELL4", conf=0.82)
        p3 = _blocks("NUTELLA", conf=0.95)
        result = arbitrate(p1, p2, p3)
        assert result == p3

    def test_multi_block_sums_confidence(self):
        """Multi-block results: winner per block — fuzzy on name, exact majority on price."""
        p1 = [("NUTELLA", 0.90), ("2.50", 0.91)]  # total 1.81
        p2 = [("NUT3LLA", 0.80), ("2.50", 0.88)]  # total 1.68
        p3 = [("NUTELL4", 0.75), ("2,50", 0.85)]  # total 1.60
        result = arbitrate(p1, p2, p3)
        assert result == p1

    def test_not_triggered_when_one_pass_too_dissimilar(self):
        """Single block: p1 and p2 close, p3 completely different → None."""
        p1 = _blocks("NUTELLA", conf=0.92)
        p2 = _blocks("NUT3LLA", conf=0.85)
        p3 = _blocks("JAMBON", conf=0.90)
        result = arbitrate(p1, p2, p3)
        assert result is None

    def test_not_triggered_when_all_too_dissimilar(self):
        """Three completely different products → None (same as before)."""
        result = arbitrate(
            _blocks("NUTELLA"),
            _blocks("JAMBON"),
            _blocks("LAIT"),
        )
        assert result is None

    def test_not_triggered_with_empty_pass(self):
        """An empty pass makes all-similar impossible → None."""
        p1 = _blocks("NUTELLA", conf=0.92)
        p2 = _blocks("NUT3LLA", conf=0.85)
        p3: list = []
        result = arbitrate(p1, p2, p3)
        assert result is None

    def test_tie_break_favours_first_pass(self):
        """Equal total confidence → p1 is returned (stable max, pass order)."""
        p1 = _blocks("NUTELLA", conf=0.90)
        p2 = _blocks("NUT3LLA", conf=0.90)
        p3 = _blocks("NUTELL4", conf=0.90)
        result = arbitrate(p1, p2, p3)
        assert result == p1

    def test_boundary_just_above_threshold(self):
        """Pair similarity exactly at threshold → fuzzy consensus triggers."""
        a, b = "ABCDE", "ABCDF"
        sim = SequenceMatcher(None, a, b, autojunk=False).ratio()
        assert sim >= _FUZZY_CONSENSUS_THRESHOLD
        p1 = _blocks(a, conf=0.91)
        p2 = _blocks(b, conf=0.85)
        p3 = _blocks("ABCDG", conf=0.80)
        result = arbitrate(p1, p2, p3)
        assert result == p1

    def test_boundary_just_below_threshold(self):
        """If one pair falls below the threshold, fuzzy consensus must not trigger."""
        p1 = _blocks("ABCDE", conf=0.92)
        p2 = _blocks("NUT3LLA", conf=0.85)
        p3 = _blocks("NUTELL4", conf=0.78)
        sim_12 = SequenceMatcher(None, "ABCDE", "NUT3LLA", autojunk=False).ratio()
        assert sim_12 < _FUZZY_CONSENSUS_THRESHOLD
        result = arbitrate(p1, p2, p3)
        assert result is None


class TestBlockLevelArbitration:
    """
    Block-level arbitration: each block is resolved independently.
    A single ambiguous block never invalidates a multi-block receipt.
    """

    # ── Option 1: same block count ─────────────────────────────────────────────

    def test_same_count_ocr_error_on_one_block_is_corrected(self):
        """3-block receipt, one block has OCR character substitutions → merged best."""
        p1 = [("NUTELLA 400G", 0.92), ("2.50", 0.91), ("LAIT", 0.90)]
        p2 = [("NUT3LLA 400G", 0.85), ("2.50", 0.88), ("LAIT", 0.87)]
        p3 = [("NUTELL4 400G", 0.80), ("2,50", 0.85), ("LAIT", 0.82)]
        result = arbitrate(p1, p2, p3)
        assert result is not None
        assert len(result) == 3
        # Block 0: fuzzy consensus → highest confidence
        assert result[0] == ("NUTELLA 400G", 0.92)
        # Block 1: "2.50" == "2.50" (majority) → highest conf of agreeing pair
        assert result[1] == ("2.50", 0.91)
        # Block 2: all three identical "LAIT" → highest confidence
        assert result[2] == ("LAIT", 0.90)

    def test_same_count_ambiguous_block_uses_best_confidence(self):
        """Multi-block: one block genuinely ambiguous → best confidence kept, not None."""
        p1 = [("NUTELLA", 0.92), ("HELLO", 0.70)]
        p2 = [("NUT3LLA", 0.85), ("WORLD", 0.75)]
        p3 = [("NUTELL4", 0.78), ("FOOBAR", 0.80)]
        result = arbitrate(p1, p2, p3)
        assert result is not None  # multi-block: never fully rejected
        assert result[0] == ("NUTELLA", 0.92)  # fuzzy consensus wins
        assert result[1] == ("FOOBAR", 0.80)  # best confidence on ambiguous block

    def test_same_count_single_block_genuine_ambiguity_returns_none(self):
        """Single block, no majority, not fuzzy-similar → None (pass_inverted gets a try)."""
        result = arbitrate(
            _blocks("NUTELLA", conf=0.92),
            _blocks("TOTAL", conf=0.88),
            _blocks("JAMBON", conf=0.85),
        )
        assert result is None

    def test_same_count_exact_majority_picks_higher_confidence(self):
        """2/3 blocks agree exactly → return the higher-confidence block of the agreeing pair.
        Full texts must differ so the fast-path exact match does not short-circuit."""
        # Full texts all differ → block-level triggers
        p1 = [("NUTELLA", 0.80), ("2.50", 0.91), ("LAIT", 0.90)]
        p2 = [("NUTELLA", 0.92), ("2,50", 0.88), ("LAIT", 0.87)]  # price differs
        p3 = [("NUT3LLA", 0.78), ("2.50", 0.85), ("LA1T", 0.82)]  # name + last block differ
        result = arbitrate(p1, p2, p3)
        assert result is not None
        # Block 0: "NUTELLA"=="NUTELLA" (p1,p2) → pick higher conf → p2[0]
        assert result[0] == ("NUTELLA", 0.92)
        # Block 1: "2.50"=="2.50" (p1,p3) → pick higher conf → p1[1]
        assert result[1] == ("2.50", 0.91)
        # Block 2: "LAIT"=="LAIT" (p1,p2) → pick higher conf → p1[2]
        assert result[2] == ("LAIT", 0.90)

    # ── Option 2: different block counts ──────────────────────────────────────

    def test_different_count_missing_block_in_one_pass(self):
        """p2 is missing the price block — alignment recovers both blocks."""
        p1 = [("NUTELLA", 0.92), ("2.50", 0.91)]
        p2 = [("NUTELLA", 0.88)]  # price block missing
        p3 = [("NUT3LLA", 0.80), ("2.50", 0.85)]
        result = arbitrate(p1, p2, p3)
        assert result is not None
        assert len(result) == 2
        assert result[0][0] == "NUTELLA"
        assert result[1][0] == "2.50"

    def test_different_count_extra_block_in_one_pass(self):
        """Longest pass (3 blocks) is anchor; extra block appears in final result."""
        p1 = [("NUTELLA", 0.92), ("2.50", 0.91)]
        p2 = [("NUT3LLA", 0.85), ("2.50", 0.88)]
        p3 = [("NUTELL4", 0.80), ("2.50", 0.83), ("TOTAL", 0.70)]  # extra block
        result = arbitrate(p1, p2, p3)
        assert result is not None
        assert len(result) == 3  # p3 is anchor (3 blocks)
        assert result[1][0] == "2.50"
        assert result[2][0] == "TOTAL"  # single-pass block kept as-is
