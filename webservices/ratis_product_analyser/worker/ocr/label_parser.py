from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ratis_core.utils.ean_checksum import validate_ean13_checksum

from worker.ocr.types import OcrResult

# EAN-13 (digits, word-bounded) — captured separately from EAN-8 so we can
# apply the GS1 checksum filter only to the 13-digit form. The EAN-8 path
# keeps the legacy first-match-wins behavior (no checksum V1 — rare in
# France, deferred to V2). See ARCH_cross_retailer_consensus.md § Bloc E.
_EAN13_RE = re.compile(r"\b\d{13}\b")
_EAN8_RE = re.compile(r"\b\d{8}\b")

# French decimal price: "2,50" or "2.50"
_PRICE_RE = re.compile(r"(\d{1,4}[,\.]\d{2})")

# Unit-price lines to skip: "1,20/kg", "0,50 €/100g", "prix au kilo"
_UNIT_PRICE_RE = re.compile(r"(\d[,\.]\d+\s*[€$]?/)|(prix\s+au)", re.IGNORECASE)

# Noise to skip: store info, TVA, address patterns
_SKIP_RE = re.compile(
    r"\bTVA\b|T\.V\.A|\bSARL\b|\bSAS\b|TOTAL|MONTANT|\bREF\b|\bTEL\b",
    re.IGNORECASE,
)

# Shelf price range: 0.05 – 9999.99
_PRICE_MIN = Decimal("0.05")
_PRICE_MAX = Decimal("9999.99")


@dataclass
class LabelItem:
    scanned_name: str
    price: Decimal
    product_ean: str | None = None


def _resolve_ean(ocr_result: OcrResult) -> str | None:
    """Find the single best EAN candidate across all OCR blocks.

    Strategy (Bloc E — V1 strict) :
      1. Collect every distinct ``\\d{13}`` substring across all blocks.
         Keep only those that pass ``validate_ean13_checksum``. If exactly
         one distinct valid EAN-13 is found → return it. Zero or two-plus
         distinct valid EAN-13 → no resolution from EAN-13.
      2. Fallback to EAN-8 first-match-wins (legacy behavior, no checksum).

    The ESL pyzbar→OCR fallback ordering is implemented in ``label_task``
    (Bloc D, already shipped) ; this helper covers the OCR-only path.
    Recovery via Levenshtein/similarity for partial-checksum matches is V2
    (batch reconciliation, Bloc E.2).
    """
    valid_13: list[str] = []
    seen_13: set[str] = set()
    for text, _conf in ocr_result:
        for m in _EAN13_RE.finditer(text):
            ean = m.group(0)
            if ean in seen_13:
                continue
            seen_13.add(ean)
            if validate_ean13_checksum(ean):
                valid_13.append(ean)

    if len(valid_13) == 1:
        return valid_13[0]
    if len(valid_13) >= 2:
        # Multiple distinct valid EAN-13 — V1 strict : no tie-break, leave
        # for batch reconciliation (Bloc E.2) or admin manual mapping.
        return None

    # No valid EAN-13 → try EAN-8 (no checksum V1).
    for text, _conf in ocr_result:
        m8 = _EAN8_RE.search(text)
        if m8:
            return m8.group(0)
    return None


def parse_label(ocr_result: OcrResult) -> LabelItem | None:
    """
    Extract a single product name + price from an electronic label OCR result.

    Strategy:
    - Resolve the product EAN across all OCR blocks (EAN-13 + checksum, then
      EAN-8 fallback) — see ``_resolve_ean``.
    - Strip any digit run that matched the EAN regex from each block before
      price/name extraction so the barcode number itself cannot become a
      price match or a name candidate on the same line.
    - Collect candidate prices (skip unit-price lines, out-of-range values).
    - Collect candidate product names (non-price, non-noise lines).
    - Return the highest-confidence price + first name candidate + EAN if found.

    Returns None if no valid price was found (unparseable label).
    """
    ean = _resolve_ean(ocr_result)

    prices: list[tuple[Decimal, float]] = []  # (value, ocr_confidence)
    name_candidates: list[str] = []

    for raw_text, conf in ocr_result:
        text = raw_text.strip()
        if not text:
            continue

        # Strip any \d{13} or \d{8} run from this block so the barcode digits
        # can't be misread as a price (e.g. ``"3017620422003"`` would otherwise
        # contain ``"4220.03"``-shaped substrings) or a name candidate.
        text = _EAN13_RE.sub("", text)
        text = _EAN8_RE.sub("", text).strip()
        if not text:
            continue  # block was EAN-only

        # Skip known noise
        if _SKIP_RE.search(text):
            continue

        # Skip unit-price lines (e.g. "1,20 €/kg")
        if _UNIT_PRICE_RE.search(text):
            continue

        # Price detection
        m_price = _PRICE_RE.search(text)
        if m_price:
            try:
                val = Decimal(m_price.group(1).replace(",", "."))
                if _PRICE_MIN <= val <= _PRICE_MAX:
                    prices.append((val, conf))
                    continue
            except InvalidOperation:
                pass

        # Product name candidate — must be long enough to be meaningful
        if len(text) >= 3:
            name_candidates.append(text)

    if not prices:
        return None

    # Pick price with highest OCR confidence; ties broken by highest value
    # (shelf prices are typically the most prominently printed text)
    dominant_price, _ = max(prices, key=lambda x: (x[1], x[0]))

    if not name_candidates:
        return None

    return LabelItem(
        scanned_name=name_candidates[0],
        price=dominant_price,
        product_ean=ean,
    )
