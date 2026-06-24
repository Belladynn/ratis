"""Intermarché parser validation against the real Phase-1 capture sample.

The capture file is gitignored data living in the main checkout, referenced
by absolute path. If it is absent the tests skip rather than fail.
"""

from pathlib import Path

import pytest

from parser.enseignes import intermarche

_CAPTURE = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/"
    "20260516_114945/www.intermarche.com.ndjson"
)

pytestmark = pytest.mark.skipif(not _CAPTURE.exists(), reason="capture sample not present")


@pytest.fixture(scope="module")
def products():
    return list(intermarche.parse_products(str(_CAPTURE)))


@pytest.fixture(scope="module")
def stores():
    return list(intermarche.parse_stores(str(_CAPTURE)))


def test_products_are_extracted(products):
    # 16 rayon products + 1 detail-page main product (same EAN as a rayon
    # product but resolved through the dedicated product-detail page).
    assert len(products) >= 16


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "intermarche"
        assert p.captured_at


def test_rayon_viandes_poissons_bio_has_16_products(products):
    # Anchor: the "Viandes et Poissons Bio" rayon listing holds 16 products.
    rayon = [p for p in products if p.category == "Viandes et Poissons Bio"]
    eans = {p.ean for p in rayon}
    assert len(eans) == 16, f"expected 16 distinct rayon EANs, got {len(eans)}"


def test_charal_steaks_haches_bio_present(products):
    charal = [p for p in products if p.ean == "3181232220026"]
    assert charal, "Charal Steaks hachés pur bœuf BIO 5% MG not found"
    p = charal[0]
    assert p.brand == "Charal"
    assert "Steaks hach" in p.name
    assert p.price_cents == 595  # 5,95 €
    assert p.quantity == "les 2 pièces de 100g - 200g"
    assert p.measure_unit == "kg"
    assert p.price_per_measure_cents == 2975  # 29,75 €/Kg
    assert p.available is True
    assert p.product_url.endswith("/3181232220026")
    assert p.store_ref == "07879"


def test_eans_are_13_digit_from_url(products):
    for p in products:
        if p.ean is not None:
            assert p.ean.isdigit()
            assert len(p.ean) == 13


def test_prices_are_int_cents(products):
    for p in products:
        if p.price_cents is not None:
            assert isinstance(p.price_cents, int)
            assert p.price_cents >= 0


def test_categorised_products_use_rayon_label(products):
    with_cat = [p for p in products if p.category]
    assert len(with_cat) >= 16
    # noise crumb "Voir TOUT" must never leak into the category field.
    for p in with_cat:
        assert p.category != "Voir TOUT"


def test_no_stores_extracted(stores):
    # No Intermarché store-list endpoint is present in this capture.
    assert stores == []
