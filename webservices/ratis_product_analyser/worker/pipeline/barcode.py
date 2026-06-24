"""Receipt barcode parsing for pipeline (PR-A foundation).

Wraps the legacy ``worker/pipeline/barcode_reader.py`` (pure functions)
and adapts the output to :class:`ParsedReceiptBarcode` (frozen Pydantic).

Length-inference for date/time formats matches the legacy behavior in
``worker/receipt_task._parse_barcode_date`` :

- 8-digit date → ``YYYYMMDD`` ; 6-digit → ``YYMMDD`` (century 2000).
- 4-digit time → ``HHMM`` ; 6-digit → ``HHMMSS``.

Per-retailer format definitions are loaded from the
``retailer_receipt_formats`` DB table via
``barcode_reader.load_barcode_formats`` — DB-driven, no hardcoded
formats here.

The legacy ``barcode_reader`` module remains unchanged (still used by
the v2 pipeline). PR-B will wire this module into the v3 Phase-2
flow ; PR-A only ships the foundation.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import time as time_cls
from typing import Any

from sqlalchemy.orm import Session

from worker.ocr.barcode_reader import (
    load_barcode_formats,
)
from worker.ocr.barcode_reader import (
    parse_receipt_barcode as _legacy_parse,
)
from worker.ocr.store_detector import _normalize_retailer_key
from worker.pipeline.types import ParsedReceiptBarcode

_CANONICAL_FIELDS: frozenset[str] = frozenset({"store_code", "caisse", "tx_id", "date", "time"})


def parse_receipt_barcode(
    raw: str,
    retailer: str | None,
    db: Session,
) -> ParsedReceiptBarcode:
    """Parse a raw receipt barcode into a :class:`ParsedReceiptBarcode`.

    Always returns a :class:`ParsedReceiptBarcode` (with ``raw`` set).
    Degrades gracefully :

    - ``retailer`` is ``None`` / unknown / format absent →
      ``ParsedReceiptBarcode(raw=raw, retailer_key=None, ...)``.
    - format hits but parse fails (length mismatch …) →
      ``ParsedReceiptBarcode(raw=raw, retailer_key=<key>, ...all None...)``.

    :param raw: the raw barcode string read by pyzbar.
    :param retailer: the merchant brand text (e.g. ``"Intermarché"``).
        ``None`` when no retailer could be detected upstream.
    :param db: SQLAlchemy session for the format-config lookup.
    """
    if not retailer:
        return ParsedReceiptBarcode(raw=raw)

    retailer_key = _normalize_retailer_key(retailer)
    formats = load_barcode_formats(db)

    if retailer_key not in formats:
        return ParsedReceiptBarcode(raw=raw, retailer_key=None)

    parsed = _legacy_parse(raw, retailer, formats)
    if not parsed:
        return ParsedReceiptBarcode(raw=raw, retailer_key=retailer_key)

    return ParsedReceiptBarcode(
        raw=raw,
        retailer_key=retailer_key,
        store_code=parsed.get("store_code"),
        caisse=parsed.get("caisse"),
        tx_id=parsed.get("tx_id"),
        date=_parse_date_field(parsed.get("date")),
        time=_parse_time_field(parsed.get("time")),
        extra=_extract_extra(parsed),
    )


def _parse_date_field(raw: str | None) -> date_cls | None:
    """Length-inference : 8 digits = ``YYYYMMDD``, 6 digits = ``YYMMDD``.

    Anything else (other lengths, non-digits, invalid calendar dates)
    returns ``None``. Matches the behavior of the legacy
    ``_parse_barcode_date`` in ``worker/receipt_task.py``.

    Manual int slicing (rather than ``datetime.strptime``) — the value
    is a wall-clock local date with no timezone, so we deliberately
    construct a naive :class:`datetime.date` directly.
    """
    if not raw or not raw.isdigit():
        return None
    try:
        if len(raw) == 8:
            return date_cls(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        if len(raw) == 6:
            return date_cls(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
    except ValueError:
        return None
    return None


def _parse_time_field(raw: str | None) -> time_cls | None:
    """Length-inference : 4 digits = ``HHMM``, 6 digits = ``HHMMSS``.

    Anything else (other lengths, non-digits, invalid wall-clock times)
    returns ``None``. Manual int slicing — the value is a local
    wall-clock time with no timezone.
    """
    if not raw or not raw.isdigit():
        return None
    try:
        if len(raw) == 4:
            return time_cls(int(raw[:2]), int(raw[2:4]))
        if len(raw) == 6:
            return time_cls(int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
    except ValueError:
        return None
    return None


def _extract_extra(parsed: dict[str, Any]) -> dict[str, str] | None:
    """Return any parsed field beyond the canonical 5 (forward-compat).

    Stringifies values to keep the contract narrow (the canonical fields
    have their own typed slots ; ``extra`` is for unknowns). ``None``
    signals "no non-canonical fields seen" — never an empty dict.
    """
    extra = {k: str(v) for k, v in parsed.items() if k not in _CANONICAL_FIELDS and v is not None}
    return extra or None


__all__ = [
    "parse_receipt_barcode",
]
