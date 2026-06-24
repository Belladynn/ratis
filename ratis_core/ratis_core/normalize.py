"""
OCR text normalization utilities.

Shared by ratis_batch_osm_sync (phone normalization at import) and
ratis_product_analyser (receipt header extraction).

V1: France only. country_code parameter is present for future i18n.
"""

import re

# Known OCR confusion map: letter → digit it is commonly misread as.
# Applied to numeric-context strings (phone, store_code, barcode prefix).
OCR_DIGIT_FIXES: dict[str, str] = {
    "O": "0",
    "o": "0",
    "I": "1",
    "l": "1",
    "Z": "2",
    "A": "4",
    "S": "5",
    "G": "6",
    "g": "6",
    "B": "8",
}

# Compiled separator pattern shared by both functions (DRY).
_SEPARATOR_RE = re.compile(r"[\s.\-/()]")


def normalize_numeric(text: str) -> str:
    """
    Strip separators (spaces, dashes, dots, slashes, parens) then apply
    OCR_DIGIT_FIXES.  Use for store_code, barcode prefix and any
    purely-numeric field extracted by OCR.

    text must be a string; None raises TypeError.
    """
    cleaned = _SEPARATOR_RE.sub("", text)
    return "".join(OCR_DIGIT_FIXES.get(c, c) for c in cleaned)


def normalize_phone(text: str, country_code: str = "FR") -> str | None:
    """
    Normalize a phone number string to a compact national format.

    Steps (France):
    1. Strip separators (spaces, dashes, dots, slashes, parens)
    2. Apply OCR_DIGIT_FIXES BEFORE prefix substitution so OCR-corrupted
       prefixes like "OO33..." become "0033..." before the prefix sub runs.
    3. Convert international prefix to national format (+33 / 0033 → 0)
    4. Validate format (FR: 0[1-9] + 8 digits)

    country_code parameter is reserved for V2 multi-country support.
    For now, any value other than "FR" returns None.

    Returns the normalized phone string or None if invalid / unsupported.
    """
    if not text:
        return None

    if country_code == "FR":
        stripped = _SEPARATOR_RE.sub("", text)
        # Apply OCR fixes BEFORE prefix substitution so "OO33..." becomes "0033..."
        pre_fixed = "".join(OCR_DIGIT_FIXES.get(c, c) for c in stripped)
        digits = re.sub(r"^\+33", "0", pre_fixed)
        digits = re.sub(r"^0033", "0", digits)
        if re.fullmatch(r"0[1-9]\d{8}", digits):
            return digits
        return None

    # V2: add UK (+44), DE (+49), ES (+34) etc. here
    return None
