"""Receipt barcode reading and per-retailer format parsing via pyzbar."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from pyzbar import pyzbar
from ratis_core.settings import load_settings

from worker.ocr.preprocessor import (
    pass_binarized,
    pass_clahe,
    pass_corrected,
    pass_inverted,
)
from worker.ocr.store_detector import _normalize_retailer_key

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)
_SETTINGS = load_settings()
_STORE_CFG = _SETTINGS["store_matching"]


def read_ean_barcode(image: np.ndarray) -> str | None:
    """Read the first EAN-13 or EAN-8 barcode from a label image.

    Returns a 13- or 8-digit string, or None if no EAN found.
    Ignores long receipt barcodes (≥20 digits) and non-numeric codes.
    """
    try:
        codes = pyzbar.decode(image)
    except Exception:
        _log.warning("pyzbar.decode failed — continuing without EAN", exc_info=True)
        return None
    for code in codes:
        data = code.data.decode("utf-8", errors="ignore")
        if data.isdigit() and len(data) in (8, 13):
            return data
    return None


def read_receipt_barcode(image: np.ndarray) -> str | None:
    """Read the first long numeric barcode from a receipt image."""
    min_digits: int = _STORE_CFG.get("barcode_min_digits", 20)
    try:
        codes = pyzbar.decode(image)
    except Exception:
        _log.warning("pyzbar.decode failed — continuing without barcode", exc_info=True)
        return None
    for code in codes:
        data = code.data.decode("utf-8", errors="ignore")
        if len(data) >= min_digits and data.isdigit():
            return data
    return None


def load_barcode_formats(db: "Session") -> dict[str, dict]:
    """Load all retailer receipt formats from DB. Returns {retailer_key: {length, fields}}."""
    from sqlalchemy import text

    rows = db.execute(text("SELECT retailer_key, length, fields FROM retailer_receipt_formats")).fetchall()
    return {row.retailer_key: {"length": row.length, "fields": row.fields} for row in rows}


def parse_receipt_barcode(
    raw: str,
    retailer: str | None,
    formats: dict[str, dict] | None = None,
) -> dict | None:
    """Parse raw barcode into named fields per retailer format config."""
    if retailer is None or not formats:
        return None
    retailer_key = _normalize_retailer_key(retailer)
    fmt = formats.get(retailer_key)
    if fmt is None or len(raw) != fmt["length"]:
        return None
    return {f["name"]: raw[f["start"] : f["end"]] for f in fmt["fields"]}


def _preprocess_passes(image: np.ndarray):
    """Yield original then each preprocessed variant — lazily.

    pass_corrected is computed once and reused by the three downstream passes.
    """
    yield image
    corrected = pass_corrected(image)
    yield corrected
    yield pass_clahe(corrected)
    yield pass_binarized(corrected)
    yield pass_inverted(corrected)


def read_receipt_barcode_with_fallbacks(image: np.ndarray) -> str | None:
    """Try pyzbar on original image then each preprocessor pass until barcode found.

    Lazy — stops at the first successful decode; avoids unnecessary processing.
    Pass order: original → corrected (deskew) → CLAHE → binarized → inverted.
    """
    for preprocessed in _preprocess_passes(image):
        raw = read_receipt_barcode(preprocessed)
        if raw:
            return raw
    return None


def extract_store_code(barcode_fields: dict | None) -> str | None:
    """Return store_code from parsed barcode fields, or None."""
    if barcode_fields is None:
        return None
    return barcode_fields.get("store_code")
