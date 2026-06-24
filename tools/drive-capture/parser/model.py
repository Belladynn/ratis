"""Normalised dataclasses for drive-capture price observations.

These are enseigne-agnostic: every per-enseigne parser produces the same
``ParsedProduct`` / ``ParsedStore`` shapes so the SQLite layer and the CLI
stay shared. Money is always stored as integer cents (project rule:
amounts = int-cents).
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class ParsedProduct:
    """One normalised price observation for a single product at a store.

    ``observations`` is append-only: re-capturing the same product later
    yields a new ``ParsedProduct`` with a fresh ``captured_at``.
    """

    enseigne: str
    name: str
    captured_at: str
    store_ref: str | None = None
    ean: str | None = None
    brand: str | None = None
    quantity: str | None = None
    category: str | None = None
    price_cents: int | None = None
    price_per_measure_cents: int | None = None
    measure_unit: str | None = None
    promo_price_cents: int | None = None
    promo_pct: int | None = None
    is_promo: bool = False
    product_url: str | None = None
    image_url: str | None = None
    available: bool | None = None
    enseigne_product_id: str | None = None


@dataclass
class ParsedStore:
    """A drive pickup point. Keyed on ``(enseigne, store_ref)``, upserted."""

    enseigne: str
    store_ref: str
    name: str | None = None
    city: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None


def field_names(dc: type) -> list[str]:
    """Ordered field names of a dataclass — used to build SQL DDL/inserts."""
    return [f.name for f in fields(dc)]
