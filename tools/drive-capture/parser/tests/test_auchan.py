"""Auchan parser validation against the real Phase-1 capture samples.

Two capture files are referenced by absolute path (both gitignored):
- ``_CAPTURE_PRODUCTS`` — original sample with rich product detail pages
  (Charal EAN etc.); used for all parse_products assertions.
- ``_CAPTURE_STORES`` — larger capture that includes the national directory
  (/nos-magasins, ~961 stores); used for parse_stores assertions.

Tests skip gracefully when the capture file is absent.
"""

from pathlib import Path

import pytest

from parser.enseignes import auchan

# Original capture: rich product detail pages (Charal EAN, etc.)
_CAPTURE_PRODUCTS = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/"
    "20260516_114945/www.auchan.fr.ndjson"
)

# Rich capture with full national directory (961 stores) + product tiles.
_CAPTURE_STORES = Path(
    "/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/"
    "20260516_161334/www.auchan.fr.ndjson"
)

_products_available = _CAPTURE_PRODUCTS.exists()
_stores_available = _CAPTURE_STORES.exists()


# ---------------------------------------------------------------------------
# product tests — use _CAPTURE_PRODUCTS
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def products():
    if not _products_available:
        pytest.skip("product capture sample not present")
    return list(auchan.parse_products(str(_CAPTURE_PRODUCTS)))


def test_products_are_extracted(products):
    assert len(products) >= 30


def test_all_products_have_name_and_enseigne(products):
    for p in products:
        assert p.name
        assert p.enseigne == "auchan"
        assert p.captured_at


def test_charal_paves_de_boeuf_present(products):
    charal = [p for p in products if p.ean == "3181232180801"]
    assert charal, "Charal Pavés de bœuf 3 poivres not found"
    p = charal[0]
    assert "CHARAL" in (p.brand or "").upper()
    assert "Pav" in p.name
    assert p.price_cents == 790  # 7,90 €
    assert p.store_ref == "452"
    assert p.quantity == "2x130g"
    assert p.enseigne_product_id == "C1201319"
    assert p.measure_unit == "kg"
    assert p.price_per_measure_cents == 3038


def test_charal_anchor_carries_rayon(products):
    beef = [p for p in products if p.ean == "3181232180801"]
    assert beef[0].category in ("Boeuf", "Boucherie")


def test_priced_products_are_int_cents(products):
    priced = [p for p in products if p.price_cents is not None]
    assert len(priced) >= 14
    for p in priced:
        assert isinstance(p.price_cents, int)
        assert p.price_cents > 0


def test_products_stamped_with_active_store(products):
    priced = [p for p in products if p.price_cents is not None]
    assert all(p.store_ref == "452" for p in priced)


def test_detail_page_enriches_tile_with_ean(products):
    anchor = [p for p in products if p.enseigne_product_id == "C1201319"]
    assert len(anchor) == 1
    assert anchor[0].ean == "3181232180801"


# ---------------------------------------------------------------------------
# store tests — use _CAPTURE_STORES (national directory)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stores():
    if not _stores_available:
        pytest.skip("stores capture sample not present")
    return list(auchan.parse_stores(str(_CAPTURE_STORES)))


def test_store_extracted_from_journey(stores):
    """The journey context store (ref='452') must always be present."""
    journey = [s for s in stores if s.store_ref == "452"]
    assert journey, "Journey context store (ref=452) not found"
    s = journey[0]
    assert s.enseigne == "auchan"
    assert s.city  # Courbevoie / Puteaux pickup point


def test_stores_total_count(stores):
    """National directory yields ~961 unique store_refs (s-NNNN namespace)."""
    # Journey context adds 1 store with ref="452" (numeric namespace).
    # Annuaire adds ~961 stores with ref="s-NNNN" namespace.
    assert len(stores) >= 961


def test_hirson_hyper_present(stores):
    """s-124 Auchan Hypermarché Hirson must be extracted correctly."""
    hirson = [s for s in stores if s.store_ref == "s-124"]
    assert hirson, "s-124 (Hirson Hyper) not found in stores"
    s = hirson[0]
    assert s.enseigne == "auchan"
    assert "Hirson" in s.name
    assert s.city == "Hirson"
    assert s.postal_code == "02500"
    # Annuaire does not carry GPS coordinates
    assert s.lat is None
    assert s.lng is None


def test_all_annuaire_stores_have_store_ref(stores):
    annuaire = [s for s in stores if s.store_ref.startswith("s-")]
    assert len(annuaire) >= 961
    for s in annuaire:
        assert s.store_ref.startswith("s-")
        assert s.enseigne == "auchan"


def test_annuaire_stores_have_name(stores):
    annuaire = [s for s in stores if s.store_ref.startswith("s-")]
    without_name = [s for s in annuaire if not s.name]
    assert len(without_name) == 0, str(len(without_name)) + " stores without name"


def test_no_duplicate_store_refs(stores):
    refs = [s.store_ref for s in stores]
    assert len(refs) == len(set(refs)), "Duplicate store_ref detected"


def test_annuaire_stores_no_gps(stores):
    annuaire = [s for s in stores if s.store_ref.startswith("s-")]
    for s in annuaire:
        assert s.lat is None
        assert s.lng is None


# ---------------------------------------------------------------------------
# unit tests — pure HTML extraction (no capture file needed)
# ---------------------------------------------------------------------------

# A clean (already-unescaped) place-pos block
_CLEAN_FRAGMENT = (
    '<div class="place-pos" data-type="HYPER">'
    '<div class="place-pos__wrapper">'
    '<div class="place-pos__wrapper place-pos__wrapper--row">'
    '<div class="place-pos__main-infos">'
    '<span class="place-pos__type-name">Hypermarché</span>'
    '<span class="place-pos__name">Auchan Hypermarché Hirson</span>'
    '<div class="place-pos__address">'
    '<span>Avenue De Verdun</span><span></span>'
    '<span>02500 Hirson</span>'
    '</div></div>'
    '<div class="place-pos__more">'
    '<a class="btn place-pos__btn" '
    'href="/magasins/hypermarche/auchan-hypermarche-hirson/s-124">'
    "+ d'infos</a>"
    '</div></div></div></div>'
)

# Same block double-escaped (as found raw in the NDJSON response_text).
# First level of escaping: < → &lt;, > → &gt;, " → &quot;
# Second level: & → &amp; (so &lt; → &amp;lt; etc.)
_DOUBLE_ESCAPED_FRAGMENT = (
    '&lt;div class=&amp;quot;place-pos&amp;quot; data-type=&amp;quot;HYPER&amp;quot;&gt;'
    '&lt;span class=&amp;quot;place-pos__name&amp;quot;&gt;Auchan Hypermarch&amp;eacute; Hirson&lt;/span&gt;'
    '&lt;div class=&amp;quot;place-pos__address&amp;quot;&gt;'
    '&lt;span&gt;Avenue De Verdun&lt;/span&gt;&lt;span&gt;&lt;/span&gt;'
    '&lt;span&gt;02500 Hirson&lt;/span&gt;'
    '&lt;/div&gt;'
    '&lt;a class=&amp;quot;btn place-pos__btn&amp;quot; '
    'href=&amp;quot;/magasins/hypermarche/auchan-hypermarche-hirson/s-124&amp;quot;&gt;'
    '+ d&amp;rsquo;infos&lt;/a&gt;'
)


def test_unescape_loop_stabilises_double_escaped():
    """The unescape loop must resolve double-escaped HTML in ≤3 passes."""
    import html as html_mod

    text = _DOUBLE_ESCAPED_FRAGMENT
    prev = ""
    passes = 0
    while text != prev and passes < 5:
        prev = text
        text = html_mod.unescape(text)
        passes += 1

    assert "Auchan Hypermarché Hirson" in text
    assert '/magasins/hypermarche/auchan-hypermarche-hirson/s-124' in text
    assert passes <= 3


def test_parse_store_from_clean_fragment():
    """_parse_store_from_block extracts correct fields from a clean block."""
    store = auchan._parse_store_from_block(_CLEAN_FRAGMENT)
    assert store is not None
    assert store.store_ref == "s-124"
    assert store.name == "Auchan Hypermarché Hirson"
    assert store.city == "Hirson"
    assert store.postal_code == "02500"
    assert store.lat is None
    assert store.lng is None
    assert store.enseigne == "auchan"


def test_parse_store_cp_city_split():
    """postal_code is the 5-digit prefix; city is the remainder."""
    fragment = (
        '<div class="place-pos">'
        '<span class="place-pos__name">Auchan Test</span>'
        '<div class="place-pos__address">'
        "<span>Rue de la Paix</span><span></span>"
        "<span>75001 Paris 1er</span>"
        "</div>"
        '<a class="btn place-pos__btn" href="/magasins/x/y/s-999">infos</a>'
        "</div>"
    )
    store = auchan._parse_store_from_block(fragment)
    assert store is not None
    assert store.postal_code == "75001"
    assert store.city == "Paris 1er"


def test_parse_store_malformed_address_no_crash():
    """A malformed address (missing CP/city span) must not raise."""
    fragment = (
        '<div class="place-pos">'
        '<span class="place-pos__name">Auchan Broken</span>'
        '<div class="place-pos__address">'
        "<span>Juste une ligne</span>"
        "</div>"
        '<a class="btn place-pos__btn" href="/magasins/x/y/s-888">infos</a>'
        "</div>"
    )
    store = auchan._parse_store_from_block(fragment)
    assert store is not None
    assert store.store_ref == "s-888"
    # postal_code and city may be None when address is malformed
    assert store.postal_code is None or isinstance(store.postal_code, str)


def test_parse_store_no_href_returns_none():
    """A block without a valid s-NNNN href must return None (no store_ref)."""
    fragment = (
        '<div class="place-pos">'
        '<span class="place-pos__name">Auchan NoRef</span>'
        '<div class="place-pos__address">'
        "<span>Rue Test</span><span></span><span>69000 Lyon</span>"
        "</div>"
        "</div>"
    )
    store = auchan._parse_store_from_block(fragment)
    assert store is None
