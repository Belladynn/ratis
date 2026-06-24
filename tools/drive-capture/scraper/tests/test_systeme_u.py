"""Tests for Système U parsers: parse_stores and parse_rayon."""

from __future__ import annotations

from scraper.parsers.systeme_u import parse_rayon, parse_stores

# ---------------------------------------------------------------------------
# parse_stores fixtures
# ---------------------------------------------------------------------------

STORES_RESPONSE = [
    {
        "storeId": "20066",
        "name": "U Express - Courbevoie ",
        "address": {"zipcode": "92400", "city": "Courbevoie", "countryCode": "FR"},
        "deliveryMode": [
            {"type": "LIVRAISON", "isEligible": True},
            {"type": "RETRAIT", "isEligible": True, "modeEligibility": "NONE"},
        ],
    },
    {
        "storeId": "20057",
        "name": "Super U - Puteaux ",
        "address": {"zipcode": "92800", "city": "Puteaux", "countryCode": "FR"},
        "deliveryMode": [
            {"type": "LIVRAISON", "isEligible": True},
            {"type": "RETRAIT", "isEligible": True, "modeEligibility": "NONE"},
        ],
    },
    {
        "storeId": "20099",
        "name": "U Express - Lyon",
        "address": {"zipcode": "69001", "city": "Lyon", "countryCode": "FR"},
        "deliveryMode": [
            {"type": "LIVRAISON", "isEligible": True},
        ],  # no RETRAIT → should be filtered out
    },
]


# ---------------------------------------------------------------------------
# parse_stores
# ---------------------------------------------------------------------------


def test_parse_stores_count():
    result = parse_stores(STORES_RESPONSE)
    assert len(result.stores) == 2


def test_parse_stores_drive_only():
    """All returned stores must have RETRAIT drive eligibility."""
    result = parse_stores(STORES_RESPONSE)
    assert len(result.stores) >= 1
    for s in result.stores:
        assert s.store_id.isdigit()


def test_parse_stores_first_store_id():
    result = parse_stores(STORES_RESPONSE)
    assert result.stores[0].store_id == "20066"


def test_parse_stores_city():
    result = parse_stores(STORES_RESPONSE)
    assert result.stores[0].city == "Courbevoie"


def test_parse_stores_postal_code():
    result = parse_stores(STORES_RESPONSE)
    assert result.stores[0].postal_code == "92400"


def test_parse_stores_name():
    result = parse_stores(STORES_RESPONSE)
    assert result.stores[0].name is not None
    assert "U Express" in result.stores[0].name or "Courbevoie" in result.stores[0].name


def test_parse_stores_gps_none():
    """SU store-locator JSON does not include GPS coordinates."""
    result = parse_stores(STORES_RESPONSE)
    for s in result.stores:
        assert s.lat is None
        assert s.lng is None


def test_parse_stores_empty():
    result = parse_stores([])
    assert result.stores == []


def test_parse_stores_none():
    result = parse_stores(None)
    assert result.stores == []


def test_parse_stores_no_drive_indicator():
    """Stores without RETRAIT deliveryMode should be filtered out."""
    data = [
        {
            "storeId": "99999",
            "name": "No Drive Store",
            "address": {"zipcode": "75001", "city": "Paris"},
            "deliveryMode": [
                {"type": "LIVRAISON", "isEligible": True},
            ],
        }
    ]
    result = parse_stores(data)
    assert result.stores == []


def test_parse_stores_retrait_not_eligible_filtered():
    """RETRAIT with isEligible=False should not count as drive."""
    data = [
        {
            "storeId": "88888",
            "name": "Ineligible Drive",
            "address": {"zipcode": "13001", "city": "Marseille"},
            "deliveryMode": [
                {"type": "RETRAIT", "isEligible": False},
            ],
        }
    ]
    result = parse_stores(data)
    assert result.stores == []


# ---------------------------------------------------------------------------
# parse_rayon (unchanged — still HTML)
# ---------------------------------------------------------------------------


def test_parse_rayon_count(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    assert len(result.products) == 2


def test_parse_rayon_ean(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    p = result.products[0]
    # NUTELLA EAN from fixture
    assert p.ean == "3017620425035"
    assert len(p.ean) == 13
    assert p.ean.isdigit()


def test_parse_rayon_price(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    p = result.products[0]
    # NUTELLA 1kg: 8.42 € → 842 cents
    assert p.price_cents == 842


def test_parse_rayon_all_prices_positive(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    for p in result.products:
        assert p.price_cents is not None
        assert p.price_cents > 0


def test_parse_rayon_brand(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    assert result.products[0].brand == "NUTELLA"


def test_parse_rayon_name(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    assert "NUTELLA" in result.products[0].name


def test_parse_rayon_product_url(su_rayon_html):
    result = parse_rayon(su_rayon_html)
    p = result.products[0]
    assert p.product_url is not None
    assert p.product_url.startswith("https://www.coursesu.com/p/")


def test_parse_rayon_empty():
    result = parse_rayon("")
    assert result.products == []


def test_parse_rayon_no_tiles():
    result = parse_rayon("<html><body>no tiles here</body></html>")
    assert result.products == []
