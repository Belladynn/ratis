"""Franprix parser validation against the real Phase-1 capture sample.

The capture file is gitignored data living in the main checkout, referenced
by absolute path. If it is absent the tests skip rather than fail.
"""

from pathlib import Path

import pytest

from parser.enseignes import franprix

_CAPTURE = Path("/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/20260516_114945/www.franprix.fr.ndjson")

pytestmark = pytest.mark.skipif(not _CAPTURE.exists(), reason="capture sample not present")


@pytest.fixture(scope="module")
def products():
    return list(franprix.parse_products(str(_CAPTURE)))


@pytest.fixture(scope="module")
def stores():
    return list(franprix.parse_stores(str(_CAPTURE)))


def test_products_are_extracted(products):
    assert len(products) >= 30


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "franprix"
        assert p.captured_at


def test_prices_are_int_cents(products):
    for p in products:
        if p.price_cents is not None:
            assert isinstance(p.price_cents, int)
            assert p.price_cents >= 0
        if p.promo_price_cents is not None:
            assert isinstance(p.promo_price_cents, int)
            assert p.promo_price_cents >= 0


def test_tartinable_ricotta_anchor(products):
    """Anchor: Tartinable ricotta crémeuse — Atelier Blini 140g, 7,80 €,
    promo 3 € (-45%)."""
    matches = [p for p in products if p.ean == "3292070010240"]
    assert matches, "Tartinable ricotta crémeuse not found"
    p = matches[0]
    assert "Tartinable ricotta" in p.name
    assert p.brand == "Atelier Blini"
    assert p.quantity == "140g"
    assert p.price_cents == 780  # 7,80 €
    assert p.promo_price_cents == 300  # 3 €
    assert p.promo_pct == 45
    assert p.is_promo is True


def test_products_carry_store_ref(products):
    # by_department captures all target storeId=5045
    with_store = [p for p in products if p.store_ref]
    assert with_store
    assert all(p.store_ref == "5045" for p in with_store)


def test_products_carry_category(products):
    with_cat = [p for p in products if p.category]
    assert len(with_cat) >= 15
    for p in with_cat:
        assert isinstance(p.category, str)
        assert p.category


def test_stores_are_extracted(stores):
    # Franprix /api/store ships ~901 drive points
    assert len(stores) >= 800


def test_stores_have_coordinates(stores):
    for s in stores:
        assert s.enseigne == "franprix"
        assert s.store_ref
        assert s.lat is not None
        assert s.lng is not None
