"""Tests for _models.py: to_cents money helper + ParsedResult dataclass."""

from __future__ import annotations

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

# ---------------------------------------------------------------------------
# to_cents
# ---------------------------------------------------------------------------


def test_to_cents_float():
    assert to_cents(3.59) == 359


def test_to_cents_string_comma():
    assert to_cents("3,59 €") == 359


def test_to_cents_string_dot():
    assert to_cents("3.59") == 359


def test_to_cents_int():
    assert to_cents(3) == 300


def test_to_cents_none():
    assert to_cents(None) is None


def test_to_cents_zero_float():
    assert to_cents(0.0) is None


def test_to_cents_zero_int():
    assert to_cents(0) is None


def test_to_cents_string_zero():
    assert to_cents("0,00 €") is None


def test_to_cents_large_with_thousands_separator():
    # Thousands separator is a regular space
    assert to_cents("1 234,56 €") == 123_456


def test_to_cents_bool_true():
    # bool subclasses int — must return None to avoid 1*100=100 confusion
    assert to_cents(True) is None


def test_to_cents_bool_false():
    assert to_cents(False) is None


def test_to_cents_empty_string():
    assert to_cents("") is None


def test_to_cents_non_numeric_string():
    assert to_cents("N/A") is None


def test_to_cents_negative():
    # Negative prices are accepted (e.g. discounts)
    result = to_cents(-1.50)
    assert result == -150


def test_to_cents_decimal_object():
    from decimal import Decimal

    assert to_cents(Decimal("2.99")) == 299


# ---------------------------------------------------------------------------
# ParsedResult defaults
# ---------------------------------------------------------------------------


def test_parsed_result_defaults():
    r = ParsedResult()
    assert r.stores == []
    assert r.products == []
    assert r.next_url is None
    assert r.fiche_jobs == []
    assert r.total_count is None


def test_parsed_result_stores_are_independent():
    """Default factory must produce independent lists per instance."""
    r1 = ParsedResult()
    r2 = ParsedResult()
    r1.stores.append(StoreResult(store_id="s1"))
    assert r2.stores == []


def test_store_result_minimal():
    s = StoreResult(store_id="abc")
    assert s.store_id == "abc"
    assert s.name is None
    assert s.extra == {}


def test_product_result_defaults():
    p = ProductResult(name="Banane BIO")
    assert p.ean is None
    assert p.price_cents is None
    assert p.is_promo is False
