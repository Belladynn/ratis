"""Système U (coursesu.com) parser validation against the real capture samples.

Product tests use the 20260516_114945 session (coursesu.com capture).
Store tests use the 20260516_161334 session (www.magasins-u.com annuaire).

Capture files are gitignored data referenced by absolute path. Tests skip
rather than fail when data is absent.
"""

from pathlib import Path

import pytest

from parser.enseignes import systeme_u

# --------------------------------------------------------------------------
# products — session 114945 (coursesu.com)
# --------------------------------------------------------------------------
_CAPTURE = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/"
    "20260516_114945/www.coursesu.com.ndjson"
)

pytestmark = pytest.mark.skipif(
    not _CAPTURE.exists(), reason="capture sample not present"
)


@pytest.fixture(scope="module")
def products():
    return list(systeme_u.parse_products(str(_CAPTURE)))


@pytest.fixture(scope="module")
def stores():
    return list(systeme_u.parse_stores(str(_CAPTURE)))


def test_products_are_extracted(products):
    assert len(products) >= 60


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "systeme_u"
        assert p.captured_at


def test_carpaccio_parmesan_present(products):
    charal = [p for p in products if p.ean == "3181232180504"]
    assert charal, "Carpaccio au parmesan CHARAL not found"
    p = charal[0]
    assert "CHARAL" in (p.brand or "").upper()
    assert "Carpaccio" in p.name
    assert p.price_cents == 815  # 8,15 €
    assert p.store_ref == "20066"
    assert p.enseigne_product_id == "1807304"


def test_carpaccio_carries_rayon(products):
    charal = [p for p in products if p.ean == "3181232180504"]
    assert charal[0].category  # product_cat3 / cat2 breadcrumb


def test_priced_products_are_int_cents(products):
    priced = [p for p in products if p.price_cents is not None]
    assert len(priced) >= 60
    for p in priced:
        assert isinstance(p.price_cents, int)
        assert p.price_cents > 0


def test_most_products_have_ean(products):
    with_ean = [p for p in products if p.ean]
    # tiles carry data-item-ean — nearly every product resolves an EAN
    assert len(with_ean) >= len(products) - 2


def test_products_stamped_with_active_store(products):
    assert all(p.store_ref == "20066" for p in products)


def test_per_measure_price_when_available(products):
    # at least some shelf tiles render a "/kg" unit price
    with_measure = [p for p in products if p.price_per_measure_cents is not None]
    assert with_measure
    for p in with_measure:
        assert p.measure_unit
        assert p.price_per_measure_cents > 0


def test_no_stores_extracted_without_annuaire(stores):
    # The 114945 session's sibling www.magasins-u.com.ndjson has no
    # /annuaire-magasin record -> parse_stores yields nothing.
    assert stores == []


# --------------------------------------------------------------------------
# stores — session 161334 (national annuaire www.magasins-u.com)
# --------------------------------------------------------------------------
_CAPTURE_ANNUAIRE = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/"
    "20260516_161334/www.magasins-u.com.ndjson"
)

_ANNUAIRE_SKIP = pytest.mark.skipif(
    not _CAPTURE_ANNUAIRE.exists(), reason="annuaire capture not present"
)


@pytest.fixture(scope="module")
def annuaire_stores():
    # Pass the sibling file itself as ndjson_path — parse_stores looks for
    # www.magasins-u.com.ndjson in the same folder (same file, same folder).
    return list(systeme_u.parse_stores(str(_CAPTURE_ANNUAIRE)))


@_ANNUAIRE_SKIP
def test_annuaire_store_count(annuaire_stores):
    # ~1389 stores in the national directory
    assert len(annuaire_stores) >= 1380


@_ANNUAIRE_SKIP
def test_all_stores_have_enseigne_and_ref(annuaire_stores):
    for s in annuaire_stores:
        assert s.enseigne == "systeme_u"
        assert s.store_ref


@_ANNUAIRE_SKIP
def test_store_refs_are_unique(annuaire_stores):
    refs = [s.store_ref for s in annuaire_stores]
    assert len(refs) == len(set(refs))


@_ANNUAIRE_SKIP
def test_hyperu_abbeville_present(annuaire_stores):
    abbeville = [s for s in annuaire_stores if s.store_ref == "hyperu-abbeville"]
    assert abbeville, "hyperu-abbeville not found"
    s = abbeville[0]
    assert s.name.startswith("Hyper U")
    assert s.city == "Abbeville"
    assert s.postal_code is None
    assert s.lat is None
    assert s.lng is None


@_ANNUAIRE_SKIP
def test_no_postal_code_or_gps(annuaire_stores):
    # The annuaire carries no postal code or GPS per store
    for s in annuaire_stores:
        assert s.postal_code is None
        assert s.lat is None
        assert s.lng is None


# --------------------------------------------------------------------------
# unit tests — pure HTML parsing (no capture file needed)
# --------------------------------------------------------------------------
_ANNUAIRE_HTML_FRAGMENT = """\
<ul aria-describedby="80_">
  <li class="u-list-magasin__item">
    <a class="u-list-magasin__link" href="https://www.magasins-u.com/magasin/hyperu-abbeville">
      <span class="title">Hyper U ABBEVILLE </span>
      <span class="icon"><img src="chevron.svg"/></span>
    </a>
  </li>
  <li class="u-list-magasin__item">
    <a class="u-list-magasin__link" href="https://www.magasins-u.com/magasin/superu-laiz">
      <span class="title">Super U LAIZ</span>
      <span class="icon"><img src="chevron.svg"/></span>
    </a>
  </li>
  <li class="u-list-magasin__item">
    <a class="u-list-magasin__link" href="https://www.magasins-u.com/magasin/uexpress-paris-15">
      <span class="title">U Express PARIS 15</span>
      <span class="icon"><img src="chevron.svg"/></span>
    </a>
  </li>
  <li class="u-list-magasin__item">
    <a class="u-list-magasin__link" href="https://www.magasins-u.com/magasin/utile-lyon-3">
      <span class="title">Utile LYON 3</span>
      <span class="icon"><img src="chevron.svg"/></span>
    </a>
  </li>
  <li class="u-list-magasin__item">
    <a class="u-list-magasin__link" href="https://www.magasins-u.com/magasin/marche-u-bordeaux">
      <span class="title">March&#233; U BORDEAUX</span>
      <span class="icon"><img src="chevron.svg"/></span>
    </a>
  </li>
</ul>
"""


def test_unit_store_ref_extraction():
    store = systeme_u._store_from_annuaire_link(
        "https://www.magasins-u.com/magasin/hyperu-abbeville",
        "Hyper U ABBEVILLE ",
    )
    assert store is not None
    assert store.store_ref == "hyperu-abbeville"


def test_unit_name_stripped_and_unescaped():
    store = systeme_u._store_from_annuaire_link(
        "https://www.magasins-u.com/magasin/marche-u-bordeaux",
        "March&#233; U BORDEAUX",
    )
    assert store is not None
    assert store.name == "Marché U BORDEAUX"


def test_unit_city_from_name_hyperu():
    assert systeme_u._city_from_name("Hyper U ABBEVILLE") == "Abbeville"


def test_unit_city_from_name_superu():
    assert systeme_u._city_from_name("Super U LAIZ") == "Laiz"


def test_unit_city_from_name_uexpress():
    assert systeme_u._city_from_name("U Express PARIS 15") == "Paris 15"


def test_unit_city_from_name_utile():
    assert systeme_u._city_from_name("Utile LYON 3") == "Lyon 3"


def test_unit_city_from_name_unknown_format():
    # An unknown format prefix -> city=None (don't guess)
    assert systeme_u._city_from_name("Marché U BORDEAUX") is None


def test_unit_regex_matches_all_fragment_stores():
    matches = list(systeme_u._STORE_LINK_RE.finditer(_ANNUAIRE_HTML_FRAGMENT))
    assert len(matches) == 5


def test_unit_missing_sibling_file_is_graceful(tmp_path):
    # A ndjson_path with no www.magasins-u.com.ndjson sibling -> empty, no exception
    orphan = tmp_path / "www.coursesu.com.ndjson"
    orphan.write_text("", encoding="utf-8")
    assert list(systeme_u.parse_stores(str(orphan))) == []
