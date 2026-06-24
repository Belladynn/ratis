"""Price normalisation helpers — shared by every enseigne parser.

Money is always integer cents. Conversion goes through ``Decimal`` to avoid
binary float drift (project rule: never ``int(float * 100)``).
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Matches the first number in a string: optional thousands separators
# (space / thin space / non-breaking space), decimal comma or dot.
_NUMBER_RE = re.compile(r"-?\d[\d\s  ]*(?:[.,]\d+)?")


def to_cents(value: object) -> int | None:
    """Normalise a money value to integer cents.

    Accepts ``int``, ``float``, ``Decimal`` and human strings such as
    ``"7,39 €"``, ``"7.39"``, ``"10,61 € / kg"``, ``"1 234,56 €"``.
    Returns ``None`` for empty/blank strings, ``None`` input, or any value
    with no parsable number.
    """
    if value is None:
        return None

    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None

    if isinstance(value, int):
        return value * 100

    if isinstance(value, (float, Decimal)):
        dec = Decimal(str(value))
        return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if isinstance(value, str):
        match = _NUMBER_RE.search(value)
        if not match:
            return None
        raw = match.group(0)
        # strip thousands separators, normalise decimal comma to dot
        raw = re.sub(r"[\s  ]", "", raw).replace(",", ".")
        try:
            dec = Decimal(raw)
        except InvalidOperation:
            return None
        return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return None


def promo_pct(base_cents: int | None, promo_cents: int | None) -> int | None:
    """Discount percentage (rounded) of ``promo_cents`` vs ``base_cents``.

    Returns ``None`` when either input is missing, the base is zero, or the
    promo price is not actually lower than the base.
    """
    if base_cents is None or promo_cents is None:
        return None
    if base_cents <= 0:
        return None
    if promo_cents > base_cents:
        return None
    discount = (base_cents - promo_cents) / base_cents * 100
    return round(discount)
