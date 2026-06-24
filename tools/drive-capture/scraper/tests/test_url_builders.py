"""Tests for scraper/url_builders.py — URL construction helpers."""

from __future__ import annotations

from scraper.url_builders import (
    carrefour_rayon_url,
    carrefour_rayon_urls,
    itm_rayon_url,
    itm_rayon_urls_for_store,
)

# ---------------------------------------------------------------------------
# ITM
# ---------------------------------------------------------------------------


def test_itm_rayon_url_absolute_path():
    """Absolute paths (starting with /) are appended directly to the base."""
    assert itm_rayon_url("/boutique/2214") == "https://www.intermarche.com/boutique/2214"


def test_itm_rayon_url_absolute_path_page2():
    url = itm_rayon_url("/boutique/2214", page=2)
    assert url == "https://www.intermarche.com/boutique/2214?page=2"


def test_itm_rayon_url_page1_no_query_param():
    """Page 1 must not append ?page=1."""
    url = itm_rayon_url("/boutique/2214", page=1)
    assert "?" not in url


def test_itm_rayon_url_legacy_relative_path():
    """Legacy relative paths are prepended with /rayons/."""
    url = itm_rayon_url("mon-marche-frais/15060")
    assert url == "https://www.intermarche.com/rayons/mon-marche-frais/15060"


def test_itm_rayon_urls_for_store_uses_catalog():
    """itm_rayon_urls_for_store must return absolute /boutique/ paths."""
    urls = itm_rayon_urls_for_store()
    assert len(urls) >= 1
    for url in urls:
        assert url.startswith("https://www.intermarche.com/boutique/")


# ---------------------------------------------------------------------------
# Carrefour
# ---------------------------------------------------------------------------


def test_carrefour_rayon_url_page1():
    assert carrefour_rayon_url("fruits-et-legumes") == "https://www.carrefour.fr/r/fruits-et-legumes"


def test_carrefour_rayon_url_page3():
    assert (
        carrefour_rayon_url("fruits-et-legumes", page=3)
        == "https://www.carrefour.fr/r/fruits-et-legumes?page=3"
    )


def test_carrefour_rayon_url_page1_no_query_param():
    """Page 1 must not append ?page=1."""
    url = carrefour_rayon_url("epicerie-sucree", page=1)
    assert "?" not in url


def test_carrefour_rayon_urls_uses_slug():
    """carrefour_rayon_urls() must use slug from catalog."""
    urls = carrefour_rayon_urls()
    assert len(urls) >= 1
    for url in urls:
        assert url.startswith("https://www.carrefour.fr/r/")
