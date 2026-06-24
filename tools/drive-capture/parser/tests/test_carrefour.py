"""Carrefour parser validation against the real Phase-1 capture sample.

The capture file is gitignored data living in the main checkout, referenced
by absolute path. If it is absent the tests skip rather than fail.
"""

from pathlib import Path

import pytest

from parser.enseignes import carrefour

_CAPTURE = Path("/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/20260516_101440/www.carrefour.fr.ndjson")

pytestmark = pytest.mark.skipif(not _CAPTURE.exists(), reason="capture sample not present")


@pytest.fixture(scope="module")
def products():
    return list(carrefour.parse_products(str(_CAPTURE)))


@pytest.fixture(scope="module")
def stores():
    return list(carrefour.parse_stores(str(_CAPTURE)))


def test_products_are_extracted(products):
    assert len(products) >= 30


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "carrefour"
        assert p.captured_at


def test_charal_paves_de_boeuf_present(products):
    charal = [p for p in products if p.ean == "3181232180801"]
    assert charal, "Charal Pavés de bœuf 3 poivres not found"
    p = charal[0]
    assert "Charal" in p.brand or "CHARAL" in p.brand
    assert "Pav" in p.name
    assert p.price_cents == 739  # ~7,39 €
    assert p.quantity == "les 2 pavés de 130g - 260g"
    assert p.enseigne_product_id == "1916616"
    assert p.measure_unit == "kg"
    assert p.price_per_measure_cents == 2842


def test_boucherie_product_categorised(products):
    # the Charal beef product carries its rayon (deepest category level)
    beef = [p for p in products if p.ean == "3181232180801"]
    assert beef[0].category in ("Boeuf", "Boucherie")


def test_categorised_products_use_deepest_rayon(products):
    # products that ship category data resolve to a non-empty rayon label;
    # recommendation/cross-sell zone products legitimately ship none.
    with_cat = [p for p in products if p.category]
    assert len(with_cat) >= 15
    for p in with_cat:
        assert isinstance(p.category, str)
        assert p.category


def test_prices_are_int_cents(products):
    for p in products:
        if p.price_cents is not None:
            assert isinstance(p.price_cents, int)
            assert p.price_cents >= 0


def test_stores_are_extracted(stores):
    assert len(stores) >= 1


def test_courbevoie_store_present(stores):
    courbevoie = [s for s in stores if s.store_ref == "1323"]
    assert courbevoie, "Market Courbevoie (ref 1323) not found"
    s = courbevoie[0]
    assert s.city == "Courbevoie"
    assert s.postal_code == "92400"
    assert s.lat is not None
    assert s.lng is not None
