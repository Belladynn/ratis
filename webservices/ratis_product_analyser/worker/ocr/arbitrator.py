from __future__ import annotations

import re
from difflib import SequenceMatcher

from ratis_core.settings import load_settings

from worker.ocr.types import OcrBlock, OcrResult

_OCR_CFG = load_settings()["ocr"]
_FUZZY_CONSENSUS_THRESHOLD: float = _OCR_CFG["fuzzy_consensus_threshold"]
assert 0 < _FUZZY_CONSENSUS_THRESHOLD <= 1, (
    f"ocr.fuzzy_consensus_threshold must be in (0, 1], got {_FUZZY_CONSENSUS_THRESHOLD}"
)


def _normalize(result: OcrResult) -> str:
    """Collapse all text blocks into a single normalized string for comparison."""
    joined = " ".join(text for text, _ in result)
    return re.sub(r"\s+", " ", joined).strip().upper()


def _similarity(a: str, b: str) -> float:
    # autojunk=False: disable the heuristic that marks frequent chars as junk,
    # which would underestimate similarity on long receipt strings (300+ chars).
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _total_confidence(result: OcrResult) -> float:
    return sum(conf for _, conf in result)


def _best_block(b1: OcrBlock, b2: OcrBlock, b3: OcrBlock) -> OcrBlock | None:
    """
    Pick the best block from 3 candidates.

    - Exact majority (2/3)       → higher-confidence block of the agreeing pair
    - Fuzzy consensus (all ≥ th) → highest confidence block
    - Genuine ambiguity          → None
    """
    t1, t2, t3 = b1[0].upper(), b2[0].upper(), b3[0].upper()

    if t1 == t2:
        return b1 if b1[1] >= b2[1] else b2
    if t1 == t3:
        return b1 if b1[1] >= b3[1] else b3
    if t2 == t3:
        return b2 if b2[1] >= b3[1] else b3

    if (
        _similarity(t1, t2) >= _FUZZY_CONSENSUS_THRESHOLD
        and _similarity(t1, t3) >= _FUZZY_CONSENSUS_THRESHOLD
        and _similarity(t2, t3) >= _FUZZY_CONSENSUS_THRESHOLD
    ):
        return max([b1, b2, b3], key=lambda b: b[1])

    return None


def _merge_positional(p1: OcrResult, p2: OcrResult, p3: OcrResult) -> OcrResult | None:
    """
    Option 1 — same block count: arbitrate each position independently.

    Single block: returns None on genuine ambiguity so pass_inverted gets a try.
    Multiple blocks: falls back to highest confidence on ambiguous positions —
    one bad block must not invalidate an entire receipt.
    """
    is_single = len(p1) == 1
    merged: OcrResult = []
    for b1, b2, b3 in zip(p1, p2, p3, strict=False):
        winner = _best_block(b1, b2, b3)
        if winner is None:
            if is_single:
                return None
            winner = max([b1, b2, b3], key=lambda b: b[1])
        merged.append(winner)
    return merged


def _align_to_reference(ref_texts: list[str], other_texts: list[str]) -> dict[int, int]:
    """
    Map reference-block indexes to matching indexes in other via SequenceMatcher opcodes.

    'equal'   → exact text match, aligned positionally.
    'replace' with equal span lengths → OCR variant of the same block, aligned positionally.
    """
    matcher = SequenceMatcher(None, ref_texts, other_texts, autojunk=False)
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for d in range(i2 - i1):
                mapping[i1 + d] = j1 + d
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            # Same-count replacement: both sides are OCR readings of the same region.
            for d in range(i2 - i1):
                mapping[i1 + d] = j1 + d
    return mapping


def _merge_aligned(p1: OcrResult, p2: OcrResult, p3: OcrResult) -> OcrResult | None:
    """
    Option 2 — different block counts: align p2 and p3 to the longest pass, then merge.

    Blocks present in all 3 passes → _best_block.
    Blocks present in 2 passes     → highest confidence.
    Blocks only in the anchor      → kept as-is (may be valid; do not discard).
    """
    ranked = sorted([(p1, 0), (p2, 1), (p3, 2)], key=lambda x: len(x[0]), reverse=True)
    ref, alt1, alt2 = ranked[0][0], ranked[1][0], ranked[2][0]

    ref_texts = [t.upper() for t, _ in ref]
    map_to_alt1 = _align_to_reference(ref_texts, [t.upper() for t, _ in alt1])
    map_to_alt2 = _align_to_reference(ref_texts, [t.upper() for t, _ in alt2])

    merged: OcrResult = []
    for i, b_ref in enumerate(ref):
        b1 = alt1[map_to_alt1[i]] if i in map_to_alt1 else None
        b2 = alt2[map_to_alt2[i]] if i in map_to_alt2 else None

        available = [b for b in [b_ref, b1, b2] if b is not None]
        if len(available) == 3:
            # len(available) == 3 ⟹ all three blocks are non-None (b_ref is
            # always present; b1/b2 only enter ``available`` when not None).
            assert b1 is not None
            assert b2 is not None
            winner = _best_block(b_ref, b1, b2)
            if winner is None:
                winner = max(available, key=lambda b: b[1])
        else:
            winner = max(available, key=lambda b: b[1])
        merged.append(winner)

    return merged if merged else None


def arbitrate(p1: OcrResult, p2: OcrResult, p3: OcrResult) -> OcrResult | None:
    """
    Pick the best OCR result by convergence across 3 passes.

    1. All empty               → None
    2. Exact full-text match   → majority (fast path, no block iteration)
    3. Same block count        → block-level positional merge
       - per block: exact majority > fuzzy consensus > best confidence (multi) / None (single)
    4. Different block counts  → block-level aligned merge (longest pass as anchor)
    5. No convergence          → None
    """
    if not p1 and not p2 and not p3:
        return None

    t1, t2, t3 = _normalize(p1), _normalize(p2), _normalize(p3)

    # Fast path: exact full-text match avoids block-level iteration.
    # Return the result with the highest total confidence among agreeing passes.
    if t1 == t2 == t3:
        return max([p1, p2, p3], key=_total_confidence)
    if t1 == t2:
        return p1 if _total_confidence(p1) >= _total_confidence(p2) else p2
    if t1 == t3:
        return p1 if _total_confidence(p1) >= _total_confidence(p3) else p3
    if t2 == t3:
        return p2 if _total_confidence(p2) >= _total_confidence(p3) else p3

    # Block-level arbitration (requires all passes to be non-empty).
    n1, n2, n3 = len(p1), len(p2), len(p3)
    if n1 > 0 and n2 > 0 and n3 > 0:
        if n1 == n2 == n3:
            return _merge_positional(p1, p2, p3)
        return _merge_aligned(p1, p2, p3)

    return None
