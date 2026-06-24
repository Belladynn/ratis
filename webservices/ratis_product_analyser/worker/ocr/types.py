from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

# (text, confidence) — one block returned by the OCR engine
OcrBlock = tuple[str, float]
OcrResult = list[OcrBlock]


@dataclass
class ScannedItem:
    scanned_name: str
    price: Decimal
    quantity: Decimal = field(default_factory=lambda: Decimal("1"))
    tva_amount: Decimal | None = None
    # LLM-denoised cashier-format text (Phase 2h). Populated by the v2
    # path on accepted clusters (similarity guard pass) ; None when the
    # guard rejected the LLM correction OR when the item was produced by
    # the legacy ``parse_receipt`` fallback. Used downstream by Phase 4
    # (scan-history UI) to display the cleaner name without lying to the
    # cache when the LLM might have hallucinated.
    corrected_name: str | None = None


@dataclass
class ReceiptData:
    items: list[ScannedItem] = field(default_factory=list)
    total_amount: Decimal | None = None
    purchased_at: date | None = None
    # Time extracted from the same OCR line as the date (HH:MM[:SS]).
    # None when the receipt doesn't show a time or OCR couldn't read it.
    purchased_at_time: time | None = None
