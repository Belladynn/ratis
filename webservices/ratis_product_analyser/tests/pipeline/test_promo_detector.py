"""Unit tests for ``pipeline.promo_detector`` (Phase C-4).

The detector is pure functional — no DB, no fixtures needed.

The dedup-by-pattern rule is the most subtle semantic in this module
and gets the most test surface : we want operators to be able to read
the test list and understand exactly what fires when.
"""

from __future__ import annotations

import pytest
from worker.pipeline.promo_detector import (
    DEFAULT_PROMO_PATTERNS,
    detect_promos,
)

# ── Single-pattern positive cases ────────────────────────────────────


def test_promo_keyword_fires():
    """``PROMO`` keyword on its own line → 1 signal."""
    text = "Coca cola 1,50€\nPROMO -0,50€"
    out = detect_promos(text)
    # Two distinct patterns match : "PROMO" → \bpromo\b, "-0,50€" → neg-price.
    assert len(out) == 2
    pats = {m.pattern for m in out}
    assert r"\bpromo\b" in pats
    assert r"-\s?\d+(?:[,.]\d+)?\s?[€%]" in pats


def test_remise_keyword_fires():
    text = "tomates 2.50€\nRemise fidélité -1.00€"
    out = detect_promos(text)
    # remise + neg-price.
    assert len(out) == 2
    pats = {m.pattern for m in out}
    assert r"\bremise\b" in pats
    assert r"-\s?\d+(?:[,.]\d+)?\s?[€%]" in pats


def test_reduction_with_accent():
    """``Réduction`` and ``Reduction`` both fire ; the char-class on
    the ``é`` covers OCR-accent-strip drift."""
    out_a = detect_promos("yaourt 1.20€\nReduction 30%")
    out_b = detect_promos("yaourt 1.20€\nRéduction 30%")
    assert any(m.pattern == r"\br[ée]duction\b" for m in out_a)
    assert any(m.pattern == r"\br[ée]duction\b" for m in out_b)


def test_soldes_fires():
    out = detect_promos("lait 2.00€\nSolde 50%\nOffre du jour")
    pats = {m.pattern for m in out}
    assert r"\bsoldes?\b" in pats
    assert r"\boffre\b" in pats


def test_economie_pattern_fires():
    """Carrefour-style ``Economie 2,50€`` — handled by the
    ``\\beconomies?\\b`` pattern (accent-permissive char-class)."""
    out = detect_promos("Pain 1.30€\nEconomie : 2,50€")
    pats = {m.pattern for m in out}
    # The economie pattern AND the neg-price pattern *do not* match
    # here — there's no leading '-'. So we expect just the economie one.
    assert r"\b[ée]conomies?\b\s*:?\s*\d+" in pats


# ── Negative cases ──────────────────────────────────────────────────


def test_no_signal_on_plain_receipt():
    """A receipt with neither promo keywords nor negative prices → 0."""
    out = detect_promos("lait 2.00€\nbeurre 4.50€\nyaourt 1.20€")
    assert out == []


def test_empty_text_yields_empty():
    assert detect_promos("") == []


def test_none_like_input_yields_empty():
    """Defensive : whitespace-only is treated as empty."""
    # ``""`` short-circuits to []. A whitespace-only string runs the
    # regexes but matches nothing on the default patterns.
    out = detect_promos("\n   \t  \n")
    assert out == []


# ── Dedup-by-pattern semantics ──────────────────────────────────────


def test_same_pattern_multiple_lines_counts_once():
    """Three occurrences of ``Reduction 30%`` → 1 signal."""
    text = "yaourt 1.20€\nReduction 30%\nReduction 30%\nReduction 30%"
    out = detect_promos(text)
    reductions = [m for m in out if m.pattern == r"\br[ée]duction\b"]
    assert len(reductions) == 1


def test_two_signals_count_separately():
    """Two different patterns each matching once → 2 signals."""
    text = "lait 2.00€\nPROMO\nRemise 10%"
    out = detect_promos(text)
    assert len(out) == 2
    pats = {m.pattern for m in out}
    assert r"\bpromo\b" in pats
    assert r"\bremise\b" in pats


def test_four_pattern_combo_counts_four():
    """Stress : 4 distinct patterns on the same receipt → 4 signals."""
    text = "PROMO Carrefour\nRemise applique : -1,50€\nSoldes d'ete -30%\nOffre du jour"
    out = detect_promos(text)
    # promo + remise + neg-price + soldes + offre = 5 distinct patterns.
    pats = {m.pattern for m in out}
    assert r"\bpromo\b" in pats
    assert r"\bremise\b" in pats
    assert r"\bsoldes?\b" in pats
    assert r"\boffre\b" in pats
    assert r"-\s?\d+(?:[,.]\d+)?\s?[€%]" in pats
    # Exactly 5 signals — order-dependent assertion.
    assert len(out) == 5


# ── Case sensitivity ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "PROMO 10%",
        "promo 10%",
        "Promo 10%",
        "pRoMo 10%",
    ],
)
def test_case_insensitive_promo(text):
    out = detect_promos(text)
    assert any(m.pattern == r"\bpromo\b" for m in out), out


# ── Negative price pattern variants ─────────────────────────────────


@pytest.mark.parametrize(
    "frag,expected",
    [
        ("-10€", True),  # tight, no space
        ("- 10€", True),  # one space
        ("-5,50€", True),  # comma decimal
        ("-5.50€", True),  # dot decimal
        ("-20%", True),  # percent
        ("- 0,50 €", True),  # trailing space before €
        ("10€", False),  # no minus → not a promo
        ("+5€", False),  # plus is not minus
    ],
)
def test_negative_price_pattern_variants(frag, expected):
    """The neg-price regex covers the main French receipt shapes
    without going overboard (must avoid matching positive prices)."""
    text = f"Produit X 5,00€\n{frag}\nTotal 10,00€"
    out = detect_promos(text)
    fired = any(m.pattern == r"-\s?\d+(?:[,.]\d+)?\s?[€%]" for m in out)
    assert fired is expected, (frag, out)


# ── Feature flag rollback ───────────────────────────────────────────


def test_enable_false_returns_empty():
    """The ``enable`` flag short-circuits to ``[]`` regardless of
    input. Operators can disable the entire detector via
    ``ratis_settings.json § pipeline.promo_detection.enable=false``
    without a code revert."""
    text = "PROMO Carrefour\nRemise -1,50€"
    out = detect_promos(text, enable=False)
    assert out == []


# ── Custom patterns ─────────────────────────────────────────────────


def test_custom_patterns_override_defaults():
    """Passing ``patterns=[...]`` overrides the defaults entirely."""
    text = "PROMO 10%\nMonoprix Bonus 5%"
    # Only the custom "bonus" pattern should fire here.
    out = detect_promos(text, patterns=[r"\bbonus\b"])
    assert len(out) == 1
    assert out[0].pattern == r"\bbonus\b"
    assert "Bonus" in out[0].text


def test_empty_patterns_yields_empty():
    """An empty patterns tuple disables the detector."""
    out = detect_promos("PROMO 10%\nRemise -1€", patterns=())
    assert out == []


# ── Malformed pattern survival ──────────────────────────────────────


def test_malformed_pattern_skipped_silently():
    """If a settings-supplied pattern is malformed, the detector
    skips it rather than crashing the receipt flow. Defense-in-depth :
    a bad ops edit to ``ratis_settings.json`` should never prevent
    receipt acceptance."""
    text = "PROMO 10%\nRemise -1€"
    # Mix one malformed regex with one valid one.
    out = detect_promos(text, patterns=[r"[unclosed", r"\bpromo\b"])
    assert len(out) == 1
    assert out[0].pattern == r"\bpromo\b"


# ── PromoMatch shape ────────────────────────────────────────────────


def test_promo_match_carries_pattern_and_text():
    out = detect_promos("Lait 2€\nPROMO Carrefour\nFin")
    promos = [m for m in out if m.pattern == r"\bpromo\b"]
    assert len(promos) == 1
    assert promos[0].pattern == r"\bpromo\b"
    # The matched substring is the regex's group(0) (just "PROMO").
    assert "PROMO" in promos[0].text


# ── Default patterns sanity ─────────────────────────────────────────


def test_default_patterns_are_non_empty():
    """The bundled defaults must cover at least the 7 documented
    French shapes. If this fails, the audit ARCH text is stale."""
    assert len(DEFAULT_PROMO_PATTERNS) >= 7


def test_default_patterns_all_compile():
    """Every bundled default must compile — guards against a copy-
    paste typo silently breaking detection at runtime."""
    import re as _re

    for pat in DEFAULT_PROMO_PATTERNS:
        _re.compile(pat, _re.IGNORECASE)
