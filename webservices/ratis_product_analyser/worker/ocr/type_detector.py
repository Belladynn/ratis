from __future__ import annotations

import re
from typing import Literal

from ratis_core.settings import load_settings

from worker.ocr.types import OcrResult

# Reuse same patterns as parser.py for consistency
_TOTAL_RE = re.compile(r"^\s*(?:TOTAL|MONTANT)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{2}[/\-\.]\d{2}[/\-\.]\d{4}\b")
_PRICE_RE = re.compile(r"\d{1,4}[,\.]\d{2}")

# Thresholds — loaded from settings (app_settings DB table or ratis_settings.json fallback)
_settings = load_settings()
try:
    _RECEIPT_MIN_PRICES: int = _settings["type_detector"]["receipt_min_prices"]
    _RECEIPT_DATE_PRICES: int = _settings["type_detector"]["receipt_date_prices"]
except KeyError as exc:
    raise KeyError(
        f"Missing key {exc} in settings — expected: "
        "type_detector.receipt_min_prices, type_detector.receipt_date_prices "
        "(check app_settings table or ratis_settings.json)"
    ) from exc


def detect_content_type(
    ocr_result: OcrResult,
    hint: str = "label",
) -> Literal["receipt", "label"]:
    """
    Infer whether OCR output comes from a receipt or an electronic label.

    Receipt signals (in order of certainty):
    1. TOTAL / MONTANT line present → receipt (unambiguous)
    2. Date pattern + >= 3 price lines → receipt
    3. >= 4 price lines without date → receipt (dense ticket)

    ``hint`` is used only as a tiebreaker when none of the above fire.
    """
    texts = [text.strip() for text, _ in ocr_result if text.strip()]

    has_total = any(_TOTAL_RE.search(t) for t in texts)
    if has_total:
        return "receipt"

    has_date = any(_DATE_RE.search(t) for t in texts)
    price_count = sum(1 for t in texts if _PRICE_RE.search(t))

    if has_date and price_count >= _RECEIPT_DATE_PRICES:
        return "receipt"

    if price_count >= _RECEIPT_MIN_PRICES:
        return "receipt"

    # Ambiguous — trust the hint
    return hint if hint in ("receipt", "label") else "label"  # type: ignore[return-value]
