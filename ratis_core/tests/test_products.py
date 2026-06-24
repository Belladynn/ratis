"""Tests for ratis_core.products.pick_display_name — pure unit, no DB."""

from __future__ import annotations

import pytest
from ratis_core.products import pick_display_name

# ── happy-path preferences ────────────────────────────────────────────────────


def test_prefers_product_name_fr_over_all_others():
    row = {
        "product_name_fr": "Yaourt à boire fraise",
        "product_name": "Strawberry yogurt drink",
        "generic_name_fr": "Yaourt à boire",
        "brands_text": "Hipro",
        "quantity_text": "4 x 250 g",
        "name": "Hipro +",
    }
    assert pick_display_name(row) == "Yaourt à boire fraise"


def test_falls_back_to_product_name_when_fr_missing():
    row = {
        "product_name_fr": None,
        "product_name": "Strawberry yogurt drink",
        "generic_name_fr": "Yaourt à boire",
        "name": "Hipro +",
    }
    assert pick_display_name(row) == "Strawberry yogurt drink"


def test_falls_back_to_generic_name_fr_when_others_missing():
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": "Yaourt à boire saveur fraise",
        "name": "Hipro +",
    }
    assert pick_display_name(row) == "Yaourt à boire saveur fraise"


def test_composite_brands_quantity_when_no_name_fields():
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": None,
        "brands_text": "Hipro",
        "quantity_text": "4 x 250 g",
        "name": "Hipro +",
    }
    assert pick_display_name(row) == "Hipro 4 x 250 g"


def test_falls_back_to_raw_name_when_only_name_present():
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": None,
        "brands_text": None,
        "quantity_text": None,
        "name": "Hipro +",
    }
    assert pick_display_name(row) == "Hipro +"


# ── candidate filtering ───────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "  ", "Bio", "OK", "Hi"])
def test_short_candidate_is_skipped(value):
    """Short / whitespace-only candidates fall through to next preference."""
    row = {
        "product_name_fr": value,
        "product_name": "Strawberry yogurt drink",
        "name": "fallback",
    }
    assert pick_display_name(row) == "Strawberry yogurt drink"


def test_whitespace_stripped_in_returned_value():
    row = {"product_name_fr": "  Nutella 400g  ", "name": "raw"}
    assert pick_display_name(row) == "Nutella 400g"


def test_non_string_candidate_skipped():
    row = {
        "product_name_fr": 12345,  # not a string
        "product_name": "Strawberry yogurt",
        "name": "fallback",
    }
    assert pick_display_name(row) == "Strawberry yogurt"


# ── composite fallback edge cases ─────────────────────────────────────────────


def test_composite_skipped_if_brands_missing():
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": None,
        "brands_text": None,
        "quantity_text": "4 x 250 g",
        "name": "raw fallback",
    }
    assert pick_display_name(row) == "raw fallback"


def test_composite_skipped_if_quantity_missing():
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": None,
        "brands_text": "Hipro",
        "quantity_text": None,
        "name": "raw fallback",
    }
    assert pick_display_name(row) == "raw fallback"


# ── ultimate fallback — name shorter than _MIN_LEN still returned ─────────────


def test_short_raw_name_still_returned_as_last_resort():
    """Schema guarantees name is non-empty; even a 1-char name is preferred to ''."""
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": None,
        "brands_text": None,
        "quantity_text": None,
        "name": "X",
    }
    assert pick_display_name(row) == "X"


def test_empty_name_returns_empty_string_defensive():
    """All fields empty → empty string. Defensive only — schema forbids in DB."""
    row = {
        "product_name_fr": None,
        "product_name": None,
        "generic_name_fr": None,
        "brands_text": None,
        "quantity_text": None,
        "name": "",
    }
    assert pick_display_name(row) == ""


# ── ORM model support ─────────────────────────────────────────────────────────


class _FakeProduct:
    """Minimal ORM-like duck for attribute-based lookup."""

    def __init__(self, **kw):
        self.product_name_fr = kw.get("product_name_fr")
        self.product_name = kw.get("product_name")
        self.generic_name_fr = kw.get("generic_name_fr")
        self.brands_text = kw.get("brands_text")
        self.quantity_text = kw.get("quantity_text")
        self.name = kw.get("name", "fallback")


def test_supports_orm_model_via_attribute_lookup():
    p = _FakeProduct(product_name_fr="Yaourt fraise", name="raw")
    assert pick_display_name(p) == "Yaourt fraise"


def test_orm_model_falls_through_to_name():
    p = _FakeProduct(name="Raw Hipro +")
    assert pick_display_name(p) == "Raw Hipro +"


def test_orm_model_missing_optional_attrs_falls_back_cleanly():
    """A model missing the new columns (e.g. older DB row) still resolves."""

    class _LegacyProduct:
        name = "Old Product"

    assert pick_display_name(_LegacyProduct()) == "Old Product"
