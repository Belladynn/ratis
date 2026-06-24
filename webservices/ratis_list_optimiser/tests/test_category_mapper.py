"""Tests for the wave-12 ``services.category_mapper.resolve_category``.

The mapper translates a ``Product`` SQLAlchemy row into one of the six
Ratis-canonical FE categories (frais / boulangerie / epicerie /
boissons / vrac / autres) — drives the new grouped-by-section Liste tab
rendering. See ``services/category_mapper.py`` for the resolution
order.
"""

from __future__ import annotations

from ratis_core.models.product import Product
from services.category_mapper import CATEGORY_ORDER, resolve_category


def _p(**kw) -> Product:
    """Build a transient (un-persisted) Product row for in-memory tests.

    Most fields default to NULL so each test can assert on a single
    discriminator without dragging unrelated metadata along. ``ean`` is
    required for any DB-persisted scenario but the resolver works off
    Python attributes, so the value here doesn't have to satisfy the
    EAN check constraint.
    """
    kw.setdefault("ean", "0000000000000")
    kw.setdefault("name", "test")
    kw.setdefault("source", "off")
    return Product(**kw)


def test_resolve_none_returns_none():
    assert resolve_category(None) is None


def test_resolve_bakery_via_en_breads_tag():
    """OFF tag ``en:breads`` → boulangerie."""
    p = _p(categories_tags=["en:breads"])
    assert resolve_category(p) == "boulangerie"


def test_resolve_bakery_via_fr_pains_tag():
    """French-prefix variant matches the same bucket."""
    p = _p(categories_tags=["fr:pains"])
    assert resolve_category(p) == "boulangerie"


def test_resolve_bakery_takes_priority_over_fresh_storage():
    """A fresh-storage croissant is still bakery, not « frais »."""
    p = _p(
        categories_tags=["en:croissants"],
        storage_type="fresh",
    )
    assert resolve_category(p) == "boulangerie"


def test_resolve_beverages_via_en_beverages_tag():
    p = _p(categories_tags=["en:beverages", "en:sodas"])
    assert resolve_category(p) == "boissons"


def test_resolve_beverages_priority_over_fresh_storage():
    """Chilled bottled water = boissons, not « frais »."""
    p = _p(
        categories_tags=["en:waters", "en:beverages"],
        storage_type="fresh",
    )
    assert resolve_category(p) == "boissons"


def test_resolve_fresh_storage_default_frais():
    """Storage = fresh, no bakery/beverage signal → frais."""
    p = _p(categories_tags=["en:dairies", "en:yogurts"], storage_type="fresh")
    assert resolve_category(p) == "frais"


def test_resolve_frozen_storage_default_frais():
    """Storage = frozen → frais (the FE bundles frozen with fresh)."""
    p = _p(categories_tags=["en:frozen-meats"], storage_type="frozen")
    assert resolve_category(p) == "frais"


def test_resolve_internal_weighted_returns_vrac():
    """``source='internal'`` + ``unit`` set = loose-weighted SKU → vrac."""
    p = _p(ean="2000000000001", source="internal", unit="kg")
    assert resolve_category(p) == "vrac"


def test_resolve_ambient_food_returns_epicerie():
    """Shelf-stable food with categories tag falls into épicerie."""
    p = _p(
        categories_tags=["en:pastas", "en:dry-pasta"],
        storage_type="ambient",
    )
    assert resolve_category(p) == "epicerie"


def test_resolve_categories_tags_only_returns_epicerie():
    """When storage_type is NULL but categories_tags carries a food
    signal, we still emit epicerie (shelf-stable default)."""
    p = _p(categories_tags=["en:canned-foods"], storage_type=None)
    assert resolve_category(p) == "epicerie"


def test_resolve_no_signal_returns_autres():
    """No storage_type, no categories_tags, no OFF metadata → autres."""
    p = _p()
    assert resolve_category(p) == "autres"


def test_resolve_unmatched_storage_no_tags_returns_autres():
    """storage_type='unmatched' with no tags = OFF gave up → autres."""
    p = _p(storage_type="unmatched")
    assert resolve_category(p) == "autres"


def test_category_order_matches_po_spec():
    """The exported ``CATEGORY_ORDER`` must match the PO directive
    (frais → boulangerie → epicerie → boissons → vrac → autres). FE
    reads this list to render section headers in order. Locked.
    """
    assert CATEGORY_ORDER == (
        "frais",
        "boulangerie",
        "epicerie",
        "boissons",
        "vrac",
        "autres",
    )


def test_resolve_handles_lang_prefix_variations():
    """``xx:beverages`` (uncommon but seen in OFF dumps) also matches."""
    p = _p(categories_tags=["xx:beverages"])
    assert resolve_category(p) == "boissons"


def test_resolve_handles_empty_string_in_tags():
    """A defensive empty string in the tag array doesn't crash the
    keyword matcher (caught defensively in ``_any_keyword``)."""
    p = _p(categories_tags=["", "en:breads"])
    assert resolve_category(p) == "boulangerie"
