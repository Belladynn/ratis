"""Leclerc parser validation against the real Phase-1 capture sample.

The capture files are gitignored data living in the main checkout, referenced
by absolute path. If they are absent the tests skip rather than fail.
"""

from pathlib import Path

import pytest

from parser.enseignes import leclerc

_CAPTURE = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/20260516_101440/fd6-courses.leclercdrive.fr.ndjson"
)

pytestmark = pytest.mark.skipif(not _CAPTURE.exists(), reason="capture sample not present")


@pytest.fixture(scope="module")
def products():
    return list(leclerc.parse_products(str(_CAPTURE)))


@pytest.fixture(scope="module")
def stores():
    return list(leclerc.parse_stores(str(_CAPTURE)))


# --------------------------------------------------------------------------
# products
# --------------------------------------------------------------------------
def test_products_are_extracted(products):
    # the "Mon boucher" rayon of Achères carries ~21 products
    assert len(products) >= 20


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "leclerc"
        assert p.captured_at


def test_prices_are_int_cents(products):
    for p in products:
        if p.price_cents is not None:
            assert isinstance(p.price_cents, int)
            assert p.price_cents >= 0


def test_boucherie_rayon_count(products):
    # all boucherie products belong to the Achères drive (ref 077801)
    boucherie = [p for p in products if p.store_ref == "077801"]
    assert 20 <= len(boucherie) <= 23


def test_products_carry_rayon_category(products):
    # rayon name resolves from the page <title> — "Mon boucher"
    with_cat = [p for p in products if p.category]
    assert with_cat
    for p in with_cat:
        assert p.category == "Mon boucher"


def test_products_have_enseigne_product_id(products):
    for p in products:
        assert p.enseigne_product_id
        assert p.enseigne_product_id.isdigit()


def test_html_entities_decoded_in_names(products):
    # raw labels carry HTML entities (&#39; &#232; ...) — must be decoded
    for p in products:
        assert "&#" not in p.name
        if p.quantity:
            assert "&#" not in p.quantity


def test_faux_filet_ean_joined_from_fiche(products):
    # the fiche-produit page 209930 ships sCodeEAN=3664335055325; the rayon
    # product 209930 must inherit it via the iIdProduit join
    faux_filet = [p for p in products if p.enseigne_product_id == "209930"]
    assert faux_filet, "Faux-filet bœuf limousine (id 209930) not found"
    p = faux_filet[0]
    assert p.ean == "3664335055325"
    assert "Faux" in p.name or "filet" in p.name.lower()
    assert p.price_cents == 649  # 6,49 €
    assert p.measure_unit == "kg"
    assert p.price_per_measure_cents == 3606  # 36,06 € / kg


def test_some_products_have_no_ean(products):
    # only one fiche page was captured -> most rayon products have ean=None
    without_ean = [p for p in products if p.ean is None]
    assert without_ean, "expected rayon products without a captured fiche"


def test_ean_only_from_captured_fiche(products):
    # exactly the products whose fiche was captured carry an EAN
    with_ean = [p for p in products if p.ean]
    for p in with_ean:
        assert p.ean.isdigit()
        assert len(p.ean) >= 8


def test_no_promo_in_this_capture(products):
    # this boucherie capture has sPrixPromo "0,00 €" everywhere
    for p in products:
        assert p.promo_price_cents is None


# --------------------------------------------------------------------------
# stores
# --------------------------------------------------------------------------
def test_stores_are_extracted(stores):
    # the MapPoint referential lists ~1012 drive points (952 unique noPL)
    assert len(stores) >= 900


def test_acheres_store_present(stores):
    acheres = [s for s in stores if s.store_ref == "077801"]
    assert acheres, "Achères drive (ref 077801) not found"
    s = acheres[0]
    assert s.name == "Achères"
    assert s.postal_code == "78260"
    assert s.lat is not None
    assert s.lng is not None


def test_all_stores_have_enseigne_and_ref(stores):
    for s in stores:
        assert s.enseigne == "leclerc"
        assert s.store_ref


def test_store_refs_are_unique(stores):
    refs = [s.store_ref for s in stores]
    assert len(refs) == len(set(refs))


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------
def test_parse_stores_missing_sibling_file_is_graceful(tmp_path):
    # a capture file with no MapPoint sibling -> empty, no exception
    orphan = tmp_path / "fd6-courses.leclercdrive.fr.ndjson"
    orphan.write_text("", encoding="utf-8")
    assert list(leclerc.parse_stores(str(orphan))) == []


def test_initoptions_blob_absent_panel():
    assert leclerc._initoptions_blob("<html>no panel here</html>", "pnlFoo") is None
    assert leclerc._initoptions_blob(None, "pnlFoo") is None
