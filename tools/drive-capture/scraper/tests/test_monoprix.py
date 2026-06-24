"""Tests for Monoprix parsers: parse_stores and parse_rayon.

NOTE: Monoprix has NO EAN at any level — ean is always None by design.
Price structure is {"amount": "3.80", "currency": "EUR"} — a nested dict,
not a flat float.  The parser must extract price.amount before calling to_cents.
"""

from __future__ import annotations

import pytest

from scraper.parsers.monoprix import parse_rayon, parse_stores

# ---------------------------------------------------------------------------
# parse_stores
# ---------------------------------------------------------------------------


def test_parse_stores_count(monoprix_stores_json):
    result = parse_stores(monoprix_stores_json)
    assert len(result.stores) == 2


def test_parse_stores_store_id(monoprix_stores_json):
    result = parse_stores(monoprix_stores_json)
    assert result.stores[0].store_id == "9861bdd0-63ae-4788-b561-8d0322cb2a71"


def test_parse_stores_coordinates(monoprix_stores_json):
    result = parse_stores(monoprix_stores_json)
    s = result.stores[0]
    assert s.lat == pytest.approx(48.852966)
    assert s.lng == pytest.approx(2.3499022)


def test_parse_stores_region_id(monoprix_stores_json):
    result = parse_stores(monoprix_stores_json)
    for s in result.stores:
        assert s.extra.get("region_id") is not None


def test_parse_stores_postal_code(monoprix_stores_json):
    result = parse_stores(monoprix_stores_json)
    assert result.stores[0].postal_code == "75004"


def test_parse_stores_empty():
    result = parse_stores({})
    assert result.stores == []


def test_parse_stores_no_delivery_addresses():
    result = parse_stores({"deliveryAddresses": []})
    assert result.stores == []


def test_parse_stores_skips_entry_without_id():
    data = {
        "deliveryAddresses": [
            {"name": "no id", "coordinates": {"latitude": 48.0, "longitude": 2.0}},
            {
                "deliveryDestinationId": "abc-123",
                "name": "Paris 4",
                "coordinates": {"latitude": 48.852966, "longitude": 2.3499022},
                "resolvedRegionId": "region-1",
                "postalCode": "75004",
            },
        ]
    }
    result = parse_stores(data)
    assert len(result.stores) == 1
    assert result.stores[0].store_id == "abc-123"


# ---------------------------------------------------------------------------
# parse_rayon
# ---------------------------------------------------------------------------


def test_parse_rayon_count(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    assert len(result.products) == 2


def test_parse_rayon_no_ean(monoprix_rayon_json):
    """Monoprix has no EAN at any level — ean must always be None."""
    result = parse_rayon(monoprix_rayon_json)
    for p in result.products:
        assert p.ean is None, f"Unexpected EAN: {p.ean}"


def test_parse_rayon_internal_id(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    assert result.products[0].internal_id == "MPX_6813928"
    assert result.products[1].internal_id == "MPX_4042014"


def test_parse_rayon_price_from_amount_dict(monoprix_rayon_json):
    """Price must be extracted from price.amount (dict), not the dict itself."""
    result = parse_rayon(monoprix_rayon_json)
    # Antikal: 3.80 € → 380 cents
    assert result.products[0].price_cents == 380


def test_parse_rayon_second_price(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    # Napolitain: 2.09 € → 209 cents
    assert result.products[1].price_cents == 209


def test_parse_rayon_all_prices_positive(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    for p in result.products:
        assert p.price_cents is not None
        assert p.price_cents > 0


def test_parse_rayon_name(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    assert "Antikal" in result.products[0].name


def test_parse_rayon_brand(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    assert result.products[0].brand == "Antikal"


def test_parse_rayon_image_url(monoprix_rayon_json):
    result = parse_rayon(monoprix_rayon_json)
    p = result.products[0]
    assert p.image_url is not None
    assert p.image_url.startswith("https://")


def test_parse_rayon_empty():
    result = parse_rayon({})
    assert result.products == []


def test_parse_rayon_next_page_token():
    data = {
        "products": [
            {
                "retailerProductId": "MPX_001",
                "name": "Produit test",
                "price": {"amount": "1.99", "currency": "EUR"},
            }
        ],
        "nextPageToken": "abc123xyz",
    }
    result = parse_rayon(data)
    assert result.next_url == "?pageToken=abc123xyz"
