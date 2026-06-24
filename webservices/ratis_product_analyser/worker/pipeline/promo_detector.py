"""Phase C-4 — regex-based promo signal detector.

Pure-function detection layer used post-comprehend. Reads the raw OCR
receipt text and returns a list of distinct promo signals. The result
drives one ``trigger_action("promo_found", quantity=N)`` per receipt
(N = signal count) so the 4 ``promo_found`` missions in the V1 catalogue
(1 daily + 3 weekly) become reachable.

Design decision (locked) : bolt-on regex layer, **not** prompt refonte.
A prompt refonte would (a) break the V3 contract tests on the
``parsed_ticket`` schema, (b) ripple through the LLM → comprehend →
match → persist chain, and (c) couple receipt acceptance to promo
recognition which is observational only. The regex layer is
self-contained, low-risk, runtime-tunable via ``ratis_settings.json``,
and degrades to "0 promo signals" without blocking receipt processing.

The patterns live in ``ratis_settings.json § pipeline.promo_detection``
(R19 — never hardcode). A hard-coded :data:`DEFAULT_PROMO_PATTERNS`
exists ONLY as a fail-safe fallback : if the settings file is somehow
unreachable, the helper still returns a sane default (callers can also
explicitly pass ``patterns=None`` to use the defaults).

Distinctness rule
-----------------
Same pattern matching multiple lines counts once. A receipt with
``Promo 10%`` appearing on 3 lines still emits a single promo signal —
the mission cares about *presence*, not *multiplicity*. Different
patterns matching independently DO count separately, e.g. one
``PROMO`` line plus one ``-2.50€`` line = 2 signals.

Out of scope
------------
- Per-item promo (we only count per-receipt — the parsed_ticket
  schema does not carry per-item promo flags).
- Retailer-specific patterns (Auchan/Lidl format variants). The
  initial 7 patterns cover Carrefour, Monoprix, Franprix shapes.
- English-text receipts (French-only patterns ; the FR-only language
  scope is consistent with the rest of the V0/V1 product).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple

# Default patterns — kept in source as a fail-safe. The canonical source
# of truth is ``ratis_settings.json § pipeline.promo_detection.patterns``.
# Patterns are Python regex applied case-insensitively (``re.IGNORECASE``)
# by :func:`detect_promos`. They are intentionally permissive so a
# Carrefour ``PROMO 10%``, a Monoprix ``Remise fidélité``, or a Franprix
# ``-2,50€`` line all fire. Per ARCH § Évolutions Phase C-4 the false
# positive rate is tolerated : a missed promo is worse than over-counting
# (the missions exist to reward *observation* of promo shopping ;
# over-counting just makes them slightly easier — still capped by the
# weekly hard mission at 3 / week).
DEFAULT_PROMO_PATTERNS: tuple[str, ...] = (
    r"\bpromo\b",  # "PROMO ..." or "Promo:..."
    r"\bremise\b",  # "Remise 10%" or "Remise appliquée"
    r"\br[ée]duction\b",  # "Reduction" / "Réduction"
    r"-\s?\d+(?:[,.]\d+)?\s?[€%]",  # "-10€" / "- 5,50€" / "-20%"
    r"\boffre\b",  # "Offre fidélité" / "Offre du jour"
    r"\bsoldes?\b",  # "Solde 30%" / "Soldes"
    r"\b[ée]conomies?\b\s*:?\s*\d+",  # "Economie 2,50€" — Carrefour shape
)


class PromoMatch(NamedTuple):
    """A single promo signal detected in the receipt text.

    Attributes
    ----------
    pattern :
        The raw regex string that fired. Stable across runs ;
        used as the dedup key (one signal per pattern per receipt).
    text :
        The matched substring (trimmed). Kept for audit so operators
        looking at ``reward_events.payload.patterns_matched`` can
        understand *why* a receipt fired N signals.
    """

    pattern: str
    text: str


def detect_promos(
    receipt_text: str,
    *,
    patterns: Sequence[str] | None = None,
    enable: bool = True,
) -> list[PromoMatch]:
    """Scan ``receipt_text`` for distinct promo signals.

    Args
    ----
    receipt_text :
        Multi-line raw OCR text (post-correction is fine — we run on
        whatever the comprehend phase fed to the LLM).
    patterns :
        Sequence of regex strings. Each is compiled with
        ``re.IGNORECASE``. ``None`` (default) uses
        :data:`DEFAULT_PROMO_PATTERNS`. The caller normally pulls
        from ``ratis_settings.json § pipeline.promo_detection.patterns``
        so the patterns are tunable at runtime.
    enable :
        Feature flag from ``ratis_settings.json``. ``False`` short-
        circuits to ``[]`` regardless of input — rollback escape hatch.

    Returns
    -------
    list[PromoMatch]
        Each entry corresponds to one *distinct* pattern that matched
        anywhere in ``receipt_text``. Order matches
        :data:`DEFAULT_PROMO_PATTERNS` order (deterministic for tests).

    Notes
    -----
    The dedup-by-pattern rule means a receipt with 3 ``PROMO`` lines
    + 1 ``-2,50€`` line yields **2** :class:`PromoMatch` instances, not
    4. The mission cares about *presence* of promo behaviour, not
    multiplicity. Different patterns matching independently DO count
    separately (cf. test ``test_two_signals_count_separately``).
    """
    if not enable:
        return []
    if not receipt_text:
        return []

    pats = tuple(patterns) if patterns is not None else DEFAULT_PROMO_PATTERNS
    out: list[PromoMatch] = []
    for raw_pat in pats:
        try:
            compiled = re.compile(raw_pat, re.IGNORECASE)
        except re.error:
            # Malformed pattern from settings ; skip silently rather
            # than crashing the whole reward flow. An ops alert exists
            # downstream via Sentry on the wider receipt_task path.
            continue
        m = compiled.search(receipt_text)
        if m is not None:
            out.append(PromoMatch(pattern=raw_pat, text=m.group(0).strip()))
    return out


__all__ = ["DEFAULT_PROMO_PATTERNS", "PromoMatch", "detect_promos"]
