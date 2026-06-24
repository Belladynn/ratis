"""Shared dataclasses for scraper-side parsers.

Each parser receives a raw HTTP response (JSON dict or HTML string) already
fetched by the runner and returns a ``ParsedResult``.  No HTTP, no DB, no
queue interaction — pure transformation.

Money is always integer cents (project rule: amounts = int-cents).
Conversion goes through ``Decimal`` to avoid binary float drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

logger = logging.getLogger(__name__)


@dataclass
class StoreResult:
    """A drive pickup point as returned by a store-discovery API."""

    store_id: str            # enseigne-specific store identifier
    name: str | None = None
    city: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    extra: dict = field(default_factory=dict)  # enseigne-specific fields


@dataclass
class ProductResult:
    """One product observation at a drive store."""

    name: str
    ean: str | None = None            # EAN-13 if known at this phase
    internal_id: str | None = None    # enseigne internal product ID
    brand: str | None = None
    quantity: str | None = None       # packaging text
    price_cents: int | None = None    # integer cents, e.g. 359 for 3,59 €
    promo_price_cents: int | None = None
    is_promo: bool = False
    category: str | None = None
    image_url: str | None = None
    product_url: str | None = None


@dataclass
class ParsedResult:
    """The structured output of a single parse_* call.

    ``fiche_jobs`` is a list of ``{url, method, payload, product_id}`` dicts
    that the runner will enqueue as individual product-detail fetches.
    """

    stores: list[StoreResult] = field(default_factory=list)
    products: list[ProductResult] = field(default_factory=list)
    next_url: str | None = None       # next page URL (pagination)
    fiche_jobs: list[dict] = field(default_factory=list)
    total_count: int | None = None    # total items (for pagination estimation)
    # EAN enrichments from fiche phase: list of (enseigne_product_id, ean) tuples.
    # Runner does UPDATE observations SET ean=? WHERE enseigne_product_id=? — not INSERT.
    ean_updates: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Money helper
# ---------------------------------------------------------------------------

def to_cents(value: object) -> int | None:
    """Normalise a money value to integer cents.

    Accepts ``int``, ``float``, ``Decimal``, and human strings such as
    ``"3,59"``, ``"3.59"``, ``"7,39 €"``, ``"1 234,56 €"``.
    Returns ``None`` for ``None``, blank strings, zero, or any value with no
    parsable number.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value == 0:
            return None
        return value * 100
    if isinstance(value, (float, Decimal)):
        dec = Decimal(str(value))
        if dec == 0:
            return None
        return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        # strip thousands separators (space variants), normalise decimal comma
        import re
        match = re.search(r"-?\d[\d\s  ]*(?:[.,]\d+)?", cleaned)
        if not match:
            return None
        raw = match.group(0)
        raw = re.sub(r"[\s  ]", "", raw).replace(",", ".")
        try:
            dec = Decimal(raw)
        except InvalidOperation:
            return None
        if dec == 0:
            return None
        return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return None
