"""EAN-13 checksum validator.

Pure function (no DB access, no network). Used by the ESL label parser to
filter candidate EAN-13 strings extracted from OCR text — only those with a
valid checksum are forwarded as a possible product match.

EAN-13 checksum algorithm (GS1) :
  Given digits d1..d13 (left to right),
    odd_sum  = d1 + d3 + d5 + d7 + d9 + d11
    even_sum = d2 + d4 + d6 + d8 + d10 + d12
    total    = odd_sum + 3 * even_sum + d13
  The code is valid iff `total % 10 == 0`.
"""

from __future__ import annotations


def validate_ean13_checksum(ean: object) -> bool:
    """Return True if ``ean`` is a 13-digit string with a valid EAN-13 checksum.

    Strict input contract :
      - ``ean`` MUST be a ``str`` of exactly 13 ASCII digits (no whitespace,
        no separators). The caller is responsible for stripping/normalizing
        before calling.
      - Anything else (None, int, bytes, wrong length, non-digit chars)
        returns ``False`` rather than raising — the function is a yes/no
        validator, not a parser.
    """
    if not isinstance(ean, str) or len(ean) != 13 or not ean.isdigit():
        return False
    digits = [int(c) for c in ean]
    odd_sum = sum(digits[i] for i in range(0, 12, 2))
    even_sum = sum(digits[i] for i in range(1, 12, 2))
    total = odd_sum + 3 * even_sum + digits[12]
    return total % 10 == 0
