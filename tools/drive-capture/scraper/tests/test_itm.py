"""Tests for ITM (Intermarché) parsers: parse_stores and parse_rayon."""

from __future__ import annotations

import pytest

from scraper.parsers.itm import parse_rayon, parse_stores

# ---------------------------------------------------------------------------
# parse_stores
# ---------------------------------------------------------------------------


def test_parse_stores_drive_filter(itm_stores_json):
    """Only stores with DRIVE in typeLivraisonOuvert must be returned."""
    result = parse_stores(itm_stores_json)
    # Fixture has 1 DRIVE + 1 DRIVE_PIETON (no DRIVE) → 1 store expected
    assert len(result.stores) == 1
    assert result.stores[0].store_id == "07879"


def test_parse_stores_coordinates(itm_stores_json):
    result = parse_stores(itm_stores_json)
    s = result.stores[0]
    assert s.lat == pytest.approx(48.893354)
    assert s.lng == pytest.approx(2.252723)


def test_parse_stores_name(itm_stores_json):
    result = parse_stores(itm_stores_json)
    s = result.stores[0]
    assert s.name is not None
    assert "INTERMARCHE" in s.name or "Intermarche" in s.name.upper()


def test_parse_stores_city_and_postal(itm_stores_json):
    result = parse_stores(itm_stores_json)
    s = result.stores[0]
    assert s.city == "Courbevoie"
    assert s.postal_code == "92400"


def test_parse_stores_empty():
    result = parse_stores({})
    assert result.stores == []


def test_parse_stores_no_resultats():
    result = parse_stores({"resultats": []})
    assert result.stores == []


def test_parse_stores_store_id_is_string(itm_stores_json):
    result = parse_stores(itm_stores_json)
    assert isinstance(result.stores[0].store_id, str)


# ---------------------------------------------------------------------------
# parse_rayon
# ---------------------------------------------------------------------------


def test_parse_rayon_products(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    assert len(result.products) == 2


def test_parse_rayon_ean_13_digits(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    for p in result.products:
        if p.ean is not None:
            assert len(p.ean) == 13 and p.ean.isdigit(), f"Bad EAN: {p.ean!r}"


def test_parse_rayon_first_product_ean(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    # First product URL ends with /3250393139833
    p = result.products[0]
    assert p.ean == "3250393139833"


def test_parse_rayon_price(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    for p in result.products:
        assert p.price_cents is not None
        assert p.price_cents > 0


def test_parse_rayon_first_price_value(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    # Banane BIO: 2.49 € → 249 cents
    assert result.products[0].price_cents == 249


def test_parse_rayon_name_not_empty(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    for p in result.products:
        assert p.name
        assert not p.name.startswith("$")


def test_parse_rayon_empty_html():
    result = parse_rayon("")
    assert result.products == []


def test_parse_rayon_no_rsc_chunks():
    result = parse_rayon("<html><body>no rsc here</body></html>")
    assert result.products == []


def test_parse_rayon_internal_id(itm_rayon_html):
    result = parse_rayon(itm_rayon_html)
    assert result.products[0].internal_id == "188156"
    assert result.products[1].internal_id == "114941"
