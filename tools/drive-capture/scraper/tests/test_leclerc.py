"""Tests for Leclerc parsers: parse_stores, parse_infomagasin and parse_rayon."""

from __future__ import annotations

import pytest

from scraper.parsers.leclerc import parse_infomagasin, parse_rayon, parse_stores

# ---------------------------------------------------------------------------
# parse_stores
# ---------------------------------------------------------------------------


def test_parse_stores_count(leclerc_mappoint_json):
    result = parse_stores(leclerc_mappoint_json)
    assert len(result.stores) == 3


def test_parse_stores_first_store_id(leclerc_mappoint_json):
    result = parse_stores(leclerc_mappoint_json)
    assert result.stores[0].store_id == "010101"


def test_parse_stores_postal_code_zero_padded(leclerc_mappoint_json):
    result = parse_stores(leclerc_mappoint_json)
    assert result.stores[0].postal_code == "01700"


def test_parse_stores_coordinates(leclerc_mappoint_json):
    result = parse_stores(leclerc_mappoint_json)
    s = result.stores[0]
    assert s.lat == pytest.approx(45.821674)
    assert s.lng == pytest.approx(4.990454)


def test_parse_stores_name(leclerc_mappoint_json):
    result = parse_stores(leclerc_mappoint_json)
    assert result.stores[0].name == "Beynost"


def test_parse_stores_no_silo_in_mappoint(leclerc_mappoint_json):
    """Full MapPoint endpoint has no numSilo — silo must not appear in extra."""
    result = parse_stores(leclerc_mappoint_json)
    for s in result.stores:
        assert "silo" not in s.extra


def test_parse_stores_empty():
    result = parse_stores([])
    assert result.stores == []


def test_parse_stores_skips_entry_without_noPL():
    data = [
        {"name": "No ID store", "latitude": 48.0, "longitude": 2.0},
        {"noPL": "010101", "name": "Beynost", "postalCode": "01700", "latitude": 45.821674, "longitude": 4.990454},
    ]
    result = parse_stores(data)
    assert len(result.stores) == 1
    assert result.stores[0].store_id == "010101"


# ---------------------------------------------------------------------------
# parse_infomagasin
# ---------------------------------------------------------------------------

INFOMAGASIN_RESPONSE = {
    "sVersion": "1",
    "sReponse": [
        {
            "iIdPointCarte": 294,
            "rLatitude": 48.965713,
            "rLongitude": 2.060164,
            "sNoPR": "077801",
            "sNomPR": "Achères",
            "sUrlSiteCourses": "https://fd6-courses.leclercdrive.fr/magasin-077801-077801-acheres.aspx",
        }
    ],
}


def test_parse_infomagasin_extracts_silo():
    result = parse_infomagasin(INFOMAGASIN_RESPONSE)
    assert len(result.stores) == 1
    store = result.stores[0]
    assert store.store_id == "077801"
    assert store.extra["silo"] == "6"
    assert store.extra["city"] == "acheres"
    assert store.lat == pytest.approx(48.965713)


def test_parse_infomagasin_extracts_name():
    result = parse_infomagasin(INFOMAGASIN_RESPONSE)
    assert result.stores[0].name == "Achères"


def test_parse_infomagasin_extracts_site_url():
    result = parse_infomagasin(INFOMAGASIN_RESPONSE)
    assert "site_url" in result.stores[0].extra
    assert result.stores[0].extra["site_url"].startswith("https://fd6-courses.leclercdrive.fr")


def test_parse_infomagasin_empty():
    assert parse_infomagasin({}).stores == []
    assert parse_infomagasin({"sReponse": []}).stores == []


def test_parse_infomagasin_none_response():
    assert parse_infomagasin(None).stores == []


def test_parse_infomagasin_missing_url():
    """Entry with no sUrlSiteCourses still returns store with no silo/city."""
    data = {
        "sReponse": [
            {
                "sNoPR": "099901",
                "sNomPR": "Test Store",
                "rLatitude": 45.0,
                "rLongitude": 2.0,
            }
        ]
    }
    result = parse_infomagasin(data)
    assert len(result.stores) == 1
    assert result.stores[0].store_id == "099901"
    assert "silo" not in result.stores[0].extra
    assert "city" not in result.stores[0].extra


# ---------------------------------------------------------------------------
# parse_rayon
# ---------------------------------------------------------------------------


def test_parse_rayon_count(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    assert len(result.products) == 2


def test_parse_rayon_no_ean(leclerc_rayon_html):
    """Leclerc rayon has no EAN — only internal_id is available."""
    result = parse_rayon(leclerc_rayon_html)
    for p in result.products:
        assert p.ean is None, f"Unexpected EAN: {p.ean}"


def test_parse_rayon_internal_id(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    assert result.products[0].internal_id == "149039"
    assert result.products[1].internal_id == "215307"


def test_parse_rayon_price(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    # Chipolatas: 6.83 € → 683 cents
    assert result.products[0].price_cents == 683


def test_parse_rayon_all_prices_positive(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    for p in result.products:
        assert p.price_cents is not None
        assert p.price_cents > 0


def test_parse_rayon_name_not_empty(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    for p in result.products:
        assert p.name
        assert p.name.strip()


def test_parse_rayon_fiche_jobs_count(leclerc_rayon_html):
    """Every product must generate a fiche_job for EAN discovery."""
    result = parse_rayon(leclerc_rayon_html)
    assert len(result.fiche_jobs) == len(result.products)


def test_parse_rayon_fiche_jobs_shape(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    for job in result.fiche_jobs:
        assert "url" in job
        assert "product_id" in job
        assert job["url"].startswith("https://")


def test_parse_rayon_fiche_jobs_urls_match_products(leclerc_rayon_html):
    result = parse_rayon(leclerc_rayon_html)
    # First product fiche URL must contain product internal_id
    fiche_url = result.fiche_jobs[0]["url"]
    assert "149039" in fiche_url


def test_parse_rayon_empty():
    result = parse_rayon("")
    assert result.products == []


def test_parse_rayon_no_init_options():
    result = parse_rayon("<html><body>no initOptions here</body></html>")
    assert result.products == []
