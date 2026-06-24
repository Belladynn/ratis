"""Tests for Carrefour parsers: parse_stores and parse_rayon (HTML)."""

from __future__ import annotations

from scraper.parsers.carrefour import parse_rayon, parse_stores

# Minimal HTML fixture with 2 Carrefour SSR product articles
_RAYON_HTML_2_PRODUCTS = """\
<html><body>
<ul class="product-list-grid">
  <li class="product-list-grid__item">
    <article data-testId="3168930171683" class="product-list-card-plp-grid">
      <div class="product-list-card-plp-grid__infos">
        <p class="product-list-card-plp-grid__shimmer-base-price">2,20<span class="product-list-card-plp-grid__shimmer-currency">€</span></p>
        <a data-testid="product-card-title" href="/p/chips-nature-format-familial-lay-s-3168930171683">
          <h3 class="product-list-card-plp-grid__shimmer-text"> Chips Nature Format familial LAY'S </h3>
        </a>
        <p class="product-list-card-plp-grid__packaging"> le sachet de 250g </p>
      </div>
    </article>
  </li>
  <li class="product-list-grid__item">
    <article data-testId="3017620425035" class="product-list-card-plp-grid">
      <div class="product-list-card-plp-grid__infos">
        <p class="product-list-card-plp-grid__shimmer-base-price">3,49<span class="product-list-card-plp-grid__shimmer-currency">€</span></p>
        <a data-testid="product-card-title" href="/p/pate-a-tartiner-nutella-400g-3017620425035">
          <h3 class="product-list-card-plp-grid__shimmer-text"> Pâte à tartiner NUTELLA 400g </h3>
        </a>
        <p class="product-list-card-plp-grid__packaging"> le pot de 400g </p>
      </div>
    </article>
  </li>
</ul>
</body></html>"""


# ---------------------------------------------------------------------------
# parse_stores
# ---------------------------------------------------------------------------


def test_parse_stores_count(carrefour_stores_json):
    result = parse_stores(carrefour_stores_json)
    assert len(result.stores) == 2


def test_parse_stores_first_ref(carrefour_stores_json):
    result = parse_stores(carrefour_stores_json)
    assert result.stores[0].store_id == "1323"


def test_parse_stores_name(carrefour_stores_json):
    result = parse_stores(carrefour_stores_json)
    assert result.stores[0].name == "Market Courbevoie"


def test_parse_stores_gps_none(carrefour_stores_json):
    """Carrefour eligibility endpoint does not return GPS coordinates."""
    result = parse_stores(carrefour_stores_json)
    for s in result.stores:
        assert s.lat is None
        assert s.lng is None


def test_parse_stores_empty():
    result = parse_stores({})
    assert result.stores == []


def test_parse_stores_no_next_url_when_no_links(carrefour_stores_json):
    result = parse_stores(carrefour_stores_json)
    assert result.next_url is None


def test_parse_stores_next_url_from_links():
    data = {
        "data": [{"ref": "1", "name": "Store A"}],
        "links": {"next": "https://www.carrefour.fr/api/eligibility/drive?page=2"},
        "meta": {},
    }
    result = parse_stores(data)
    assert result.next_url == "https://www.carrefour.fr/api/eligibility/drive?page=2"


# ---------------------------------------------------------------------------
# parse_rayon (HTML)
# ---------------------------------------------------------------------------


def test_parse_rayon_count():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    assert len(result.products) == 2


def test_parse_rayon_ean():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    p = result.products[0]
    assert p.ean == "3168930171683"
    assert len(p.ean) == 13
    assert p.ean.isdigit()


def test_parse_rayon_price():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    # 2,20 € → 220 cents
    assert result.products[0].price_cents == 220
    # 3,49 € → 349 cents
    assert result.products[1].price_cents == 349


def test_parse_rayon_all_ean_13_digits():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    for p in result.products:
        if p.ean:
            assert len(p.ean) == 13 and p.ean.isdigit(), f"Bad EAN: {p.ean!r}"


def test_parse_rayon_name_not_empty():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    for p in result.products:
        assert p.name


def test_parse_rayon_no_fiche_jobs():
    """Carrefour has EAN at rayon level → no fiche_jobs needed."""
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    assert result.fiche_jobs == []


def test_parse_rayon_empty():
    result = parse_rayon("")
    assert result.products == []


def test_parse_rayon_quantity():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    assert result.products[0].quantity == "le sachet de 250g"


def test_parse_rayon_product_url():
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    assert result.products[0].product_url == "https://www.carrefour.fr/p/chips-nature-format-familial-lay-s-3168930171683"


def test_parse_rayon_next_url_none_under_30_products():
    """Fewer than 30 products → no more pages."""
    result = parse_rayon(_RAYON_HTML_2_PRODUCTS)
    assert result.next_url is None


def test_parse_rayon_next_url_sentinel_when_full_page():
    """Exactly 30 products → pagination sentinel '?page=next'."""
    # Build HTML with 30 products
    items = ""
    for i in range(30):
        ean = f"316893017{i:04d}"
        items += f"""
  <li><article data-testId="{ean}" class="product-list-card-plp-grid">
    <div><p class="product-list-card-plp-grid__shimmer-base-price">1,99<span>€</span></p>
    <a href="/p/product-{i}-{ean}"><h3>Product {i}</h3></a>
    </div></article></li>"""
    html = f"<ul>{items}</ul>"
    result = parse_rayon(html)
    assert len(result.products) == 30
    assert result.next_url == "?page=next"
