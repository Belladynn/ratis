"""Tests for Auchan parsers: parse_stores."""

from __future__ import annotations

from scraper.parsers.auchan import parse_stores

# ---------------------------------------------------------------------------
# parse_stores fixtures
# ---------------------------------------------------------------------------

STORES_HTML = """
<ul>
  <div class="place-pos">
    <a href="/drive/s-124">
      <span class="place-pos__name">Auchan Drive Hirson</span>
    </a>
    <div class="place-pos__address">
      <span>3 rue du Commerce</span>
      <span>Zone Artisanale</span>
      <span>02500 Hirson</span>
    </div>
  </div>
  <div class="place-pos">
    <a href="/drive/s-6087">
      <span class="place-pos__name">Auchan Drive Massieux</span>
    </a>
    <div class="place-pos__address">
      <span>01600 Massieux</span>
    </div>
  </div>
  <div class="place-pos">
    <a href="/drive/s-999">
      <!-- no name span, no address -->
    </a>
  </div>
</ul>
"""


# ---------------------------------------------------------------------------
# parse_stores
# ---------------------------------------------------------------------------


def test_parse_stores_extracts_all_drives():
    """All three place-pos blocks are extracted, including the one without name/address."""
    result = parse_stores(STORES_HTML)
    assert len(result.stores) == 3


def test_parse_stores_name_and_address():
    """Store s-124 has correct name, city, and postal_code (3-span address)."""
    result = parse_stores(STORES_HTML)
    store = next(s for s in result.stores if s.store_id == "s-124")
    assert store.name == "Auchan Drive Hirson"
    assert store.postal_code == "02500"
    assert store.city == "Hirson"


def test_parse_stores_address_one_span():
    """Store s-6087 extracts city and postal_code from a single-span address."""
    result = parse_stores(STORES_HTML)
    store = next(s for s in result.stores if s.store_id == "s-6087")
    assert store.postal_code == "01600"
    assert store.city == "Massieux"


def test_parse_stores_empty():
    """Empty string returns zero stores."""
    result = parse_stores("")
    assert len(result.stores) == 0


def test_parse_stores_no_match():
    """HTML with no place-pos blocks returns zero stores."""
    result = parse_stores("<div>rien</div>")
    assert len(result.stores) == 0
