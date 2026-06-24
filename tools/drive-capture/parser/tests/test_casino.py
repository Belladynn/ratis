"""Casino-group parser validation — www.mescoursesdeproximite.com.

Two test layers:

1. **Unit tests** — parse ld+json fragments in-memory; run everywhere, no
   capture file required.
2. **Integration tests against the real capture** — skipped when the
   gitignored capture file is absent.

Capture: ``captures/20260516_152647/www.mescoursesdeproximite.com.ndjson``
Contents at time of writing: 1 product page (Charal Steak haché,
EAN ``3181232220286``, 7,99 €) + 1 store (Le Petit Casino Levallois-Perret,
store_ref ``C1507``, CP 92300).
"""

from pathlib import Path

import pytest

from parser.enseignes import casino
from parser.enseignes._schemaorg import iter_ld_json

# ---------------------------------------------------------------------------
# integration: real capture (skipped if absent — data is gitignored)
# ---------------------------------------------------------------------------

_CAPTURE = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/"
    "20260516_152647/www.mescoursesdeproximite.com.ndjson"
)

pytestmark = pytest.mark.skipif(
    not _CAPTURE.exists(), reason="capture sample not present"
)


@pytest.fixture(scope="module")
def products():
    return list(casino.parse_products(str(_CAPTURE)))


@pytest.fixture(scope="module")
def stores():
    return list(casino.parse_stores(str(_CAPTURE)))


# -- product integration tests -----------------------------------------------

def test_products_are_extracted(products):
    """At least one product must be extracted from the capture."""
    assert len(products) >= 1


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "casino"
        assert p.captured_at


def test_prices_are_int_cents(products):
    for p in products:
        if p.price_cents is not None:
            assert isinstance(p.price_cents, int)
            assert p.price_cents >= 0


def test_charal_steak_hache_anchor(products):
    """Anchor: Charal Steak haché Frais pur Bœuf 2x130g.

    EAN 3181232220286, price 7,99 €, store C1507, InStock.
    """
    matches = [p for p in products if p.ean == "3181232220286"]
    assert matches, "Charal Steak haché (EAN 3181232220286) not found"
    p = matches[0]
    assert "Charal" in (p.brand or "")
    assert "Steak" in p.name or "steak" in p.name.lower()
    assert p.price_cents == 799  # 7,99 €
    assert p.store_ref == "C1507"
    assert p.enseigne_product_id == "174353"
    assert p.available is True
    assert "Viandes" in (p.category or "") or "Boeufs" in (p.category or "")


def test_all_products_have_price(products):
    """mescoursesdeproximite.com always includes the price in its ld+json."""
    for p in products:
        assert p.price_cents is not None, (
            f"product {p.name!r} (EAN {p.ean}) has price_cents=None — "
            "the new source should always carry a price"
        )


def test_all_products_have_ean(products):
    """Every product-detail page on mescoursesdeproximite.com has gtin13."""
    for p in products:
        assert p.ean, f"product {p.name!r} has no EAN"


# -- store integration tests --------------------------------------------------

def test_stores_are_extracted(stores):
    assert len(stores) >= 1


def test_le_petit_casino_levallois_present(stores):
    """Anchor: Le Petit Casino Levallois-Perret (C1507)."""
    matches = [s for s in stores if s.store_ref == "C1507"]
    assert matches, "Store C1507 (Le Petit Casino Levallois) not found"
    s = matches[0]
    assert s.enseigne == "casino"
    assert "Levallois" in (s.name or "") or "Levallois" in (s.city or "")
    assert s.postal_code == "92300"
    assert s.lat is not None
    assert s.lng is not None
    assert abs(s.lat - 48.891673) < 0.001
    assert abs(s.lng - 2.284639) < 0.001


def test_stores_have_coordinates(stores):
    for s in stores:
        assert s.enseigne == "casino"
        assert s.store_ref


# ---------------------------------------------------------------------------
# unit tests — pure extraction from inline HTML fragments (always run)
# ---------------------------------------------------------------------------

_PRODUCT_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "@id": "https://www.mescoursesdeproximite.com/produit/charal-steak-hache-frais-pur-boeuf-5-de-mg-2x130g-260g-le-petit-casino-92300/C1507/174353#product",
  "name": "Charal Steak haché Frais pur Bœuf, 5% de Mg 2x130g",
  "image": "https://www.mescoursesdeproximite.com/images/produits/produit/174353_M1_S1.jpg",
  "sku": "C1507174353",
  "gtin13": "3181232220286",
  "brand": {"@type": "Brand", "name": "Charal"},
  "category": "Viandes Et Poissons > Boeufs",
  "weight": {"@type": "QuantitativeValue", "value": 0.26},
  "offers": {
    "@type": "Offer",
    "url": "https://www.mescoursesdeproximite.com/produit/charal-steak-hache-frais-pur-boeuf-5-de-mg-2x130g-260g-le-petit-casino-92300/C1507/174353",
    "priceCurrency": "EUR",
    "price": 7.99,
    "availability": {"@type": "ItemAvailability", "@id": "https://schema.org/InStock"},
    "seller": {"@type": "GroceryStore", "@id": "https://www.mescoursesdeproximite.com/courses-en-ligne/le-petit-casino-92300/C1507#store"}
  }
}
</script>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "@id": "https://www.mescoursesdeproximite.com/courses-en-ligne/le-petit-casino-92300/C1507#store",
  "name": "Le Petit Casino Levallois Perret",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "3 PLACE DU GENERAL LECLERC",
    "addressLocality": "Levallois Perret",
    "addressRegion": "92",
    "postalCode": "92300",
    "addressCountry": "FR"
  },
  "geo": {"@type": "GeoCoordinates", "latitude": 48.891673, "longitude": 2.284639}
}
</script>
</head></html>
"""


def test_unit_iter_ld_json_finds_two_blocks():
    blocks = list(iter_ld_json(_PRODUCT_HTML))
    types = [b.get("@type") for b in blocks]
    assert "Product" in types
    assert "LocalBusiness" in types


def test_unit_parse_product_from_fragment():
    """_parse_product extracts all key fields correctly."""
    blocks = list(iter_ld_json(_PRODUCT_HTML))
    product_block = next(b for b in blocks if b.get("@type") == "Product")
    p = casino._parse_product(product_block, captured_at="2026-05-16T15:26:47")
    assert p is not None
    assert p.name == "Charal Steak haché Frais pur Bœuf, 5% de Mg 2x130g"
    assert p.ean == "3181232220286"
    assert p.brand == "Charal"
    assert p.price_cents == 799
    assert p.available is True
    assert p.store_ref == "C1507"
    assert p.enseigne_product_id == "174353"
    assert p.category == "Viandes Et Poissons > Boeufs"
    assert p.image_url == "https://www.mescoursesdeproximite.com/images/produits/produit/174353_M1_S1.jpg"
    assert p.product_url is not None
    assert "174353" in p.product_url
    assert p.enseigne == "casino"
    assert p.quantity is None  # not extracted (see module docstring)


def test_unit_parse_store_from_fragment():
    """_parse_store extracts address, coords and store_ref correctly."""
    blocks = list(iter_ld_json(_PRODUCT_HTML))
    lb_block = next(b for b in blocks if b.get("@type") == "LocalBusiness")
    s = casino._parse_store(lb_block)
    assert s is not None
    assert s.store_ref == "C1507"
    assert s.name == "Le Petit Casino Levallois Perret"
    assert s.city == "Levallois Perret"
    assert s.postal_code == "92300"
    assert abs(s.lat - 48.891673) < 1e-5
    assert abs(s.lng - 2.284639) < 1e-5
    assert s.enseigne == "casino"


def test_unit_out_of_stock_availability():
    """availability @id ending in OutOfStock → available=False."""
    html = """
    <script type="application/ld+json">
    {
      "@type": "Product",
      "@id": "https://www.mescoursesdeproximite.com/produit/test/C9999/99999#product",
      "name": "Test Product",
      "gtin13": "0000000000000",
      "offers": {
        "@type": "Offer",
        "price": 1.99,
        "availability": {"@type": "ItemAvailability", "@id": "https://schema.org/OutOfStock"}
      }
    }
    </script>
    """
    blocks = list(iter_ld_json(html))
    p = casino._parse_product(blocks[0], captured_at="2026-05-16")
    assert p is not None
    assert p.available is False


def test_unit_product_without_name_returns_none():
    """_parse_product returns None when name is absent."""
    obj = {
        "@type": "Product",
        "@id": "https://www.mescoursesdeproximite.com/produit/test/C9999/99999#product",
        "gtin13": "0000000000000",
        "offers": {"price": 1.99},
    }
    assert casino._parse_product(obj, captured_at="2026-05-16") is None


def test_unit_store_without_store_code_returns_none():
    """_parse_store returns None when @id has no recognisable store code."""
    obj = {
        "@type": "LocalBusiness",
        "@id": "https://www.mescoursesdeproximite.com/courses-en-ligne/some-store",
        "name": "Some store",
    }
    assert casino._parse_store(obj) is None


def test_unit_price_is_int_cents_not_float():
    """Ensure to_cents is used — price 7.99 → 799 int, never 798 from float."""
    blocks = list(iter_ld_json(_PRODUCT_HTML))
    product_block = next(b for b in blocks if b.get("@type") == "Product")
    p = casino._parse_product(product_block, captured_at="2026-05-16")
    assert p is not None
    assert isinstance(p.price_cents, int)
    assert p.price_cents == 799
