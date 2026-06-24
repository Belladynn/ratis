"""Tests for GET /api/v1/product/search (Bug 3 of wave 4).

Text search endpoint backing the Liste tab AddBar autocomplete and the
new Produit tab search input. Matches on ``products.name_normalized``
(unaccented + uppercased GENERATED column with a GIN trigram index) and
``products.brands_text``.

Sources : the wave-4 brief mentioned excluding ``source='user_suggested'``
but that source label belongs to the ``stores`` table (cf
``ARCH_store_resolution.md``), not ``products`` — the product
``source_check`` constraint is ``IN ('off', 'obp', 'opf', 'opff',
'internal')``. We therefore surface all valid sources here. The
exclusion clause in the SQL stays as forward-defence (no-op today) for
the day a ``user_suggested`` or ``pending`` product source lands.
"""

from __future__ import annotations

import pytest
from ratis_core.models.product import Product

from tests.conftest import make_token


def _auth(user):
    return {"Authorization": f"Bearer {make_token(user.id)}"}


@pytest.fixture
def search_corpus(db):
    """Seed a representative corpus for ordering / matching assertions.

    Names purposefully mix accents + casing + brand-only matches so the
    tests exercise unaccent + upper(name) normalisation and the brand
    fallback path.
    """
    rows = [
        Product(ean="3017620420001", name="Lait demi-écrémé 1L", source="off"),
        Product(ean="3017620420002", name="LAIT entier 1L", source="off"),
        Product(ean="3017620420003", name="Laìt bio 50cl", source="off"),
        Product(
            ean="3017620420004",
            name="Yaourt vanille",
            brands_text="Lactel",
            source="off",
        ),
        Product(
            ean="3017620420005",
            name="Pain de mie complet",
            source="off",
        ),
        Product(
            ean="2017620420006",
            name="Lait pesé en vrac",
            source="internal",
            unit="kg",
        ),
        Product(
            ean="3017620420007",
            name="Crème dessert lait sucré",
            source="off",
        ),
        Product(
            ean="3017620420008",
            name="Lait",
            source="off",
        ),
    ]
    for r in rows:
        db.add(r)
    db.flush()
    db.commit()
    return rows


@pytest.fixture
def potato_corpus(db):
    """Seed the « pomme de terre » duplicate scenario PO flagged.

    All rows have an identical-ish ``name`` (« Pomme de terre ») but
    differ on brand / quantity / origin / source / labels — exactly the
    discrimination signals the enriched dropdown row must surface to
    let the user pick the right one.
    """
    rows = [
        # Naked OFF row : no brand, no quantity, no origin — least
        # informative, should sink to the bottom of the ordering.
        Product(
            ean="3017620421001",
            name="Pomme de terre",
            source="off",
        ),
        # Branded + quantified + french origin — the prime candidate,
        # must surface FIRST.
        Product(
            ean="3017620421002",
            name="Pomme de terre",
            brands="Carrefour",
            brands_text="Carrefour",
            quantity_text="1 kg",
            origins_tags=["en:france"],
            source="off",
        ),
        # Branded + quantified but NO french origin — second best.
        Product(
            ean="3017620421003",
            name="Pomme de terre",
            brands="Lidl",
            brands_text="Lidl",
            quantity_text="2 kg",
            origins_tags=["en:belgium"],
            source="off",
        ),
        # Branded only (no quantity, no origin) — third.
        Product(
            ean="3017620421004",
            name="Pomme de terre",
            brands="Casino",
            brands_text="Casino",
            source="off",
        ),
        # Internal weighted row (vrac) — same name, source=internal.
        # Should sink below all OFF rows due to source-quality bucket.
        Product(
            ean="2017620421005",
            name="Pomme de terre",
            source="internal",
            unit="kg",
        ),
        # Branded + organic (labels_tags) — checks the labels_tags
        # column is round-tripped end-to-end.
        Product(
            ean="3017620421006",
            name="Pomme de terre bio",
            brands="Bio Village",
            brands_text="Bio Village",
            quantity_text="500 g",
            origins_tags=["en:france"],
            labels_tags=["en:organic", "fr:bio"],
            source="off",
        ),
    ]
    for r in rows:
        db.add(r)
    db.flush()
    db.commit()
    return rows


# ── happy path ────────────────────────────────────────────────────────────────


def test_search_returns_results(client, user, search_corpus):
    resp = client.get("/api/v1/product/search?q=lait", headers=_auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    eans = {it["ean"] for it in body["items"]}
    # All non-user_suggested rows whose name (unaccented, upper) contains
    # "LAIT" must show up. That's everything except the pain row.
    assert "3017620420001" in eans  # Lait demi-écrémé
    assert "3017620420002" in eans  # LAIT entier
    assert "3017620420003" in eans  # Laìt bio (accent)
    assert "3017620420007" in eans  # Crème dessert lait
    assert "3017620420008" in eans  # Lait (exact)
    assert "2017620420006" in eans  # internal — Lait pesé en vrac
    # Brand-only match (no "lait" in name but no brand "lait" either) —
    # the Yaourt vanille brand=Lactel should NOT match "lait" because
    # neither the name nor the brand contains the token.
    assert "3017620420004" not in eans


def test_search_response_shape(client, user, search_corpus):
    """Each hit carries the full enriched shape the FE relies on to
    distinguish duplicate-name products (« Pomme de terre » × 8).

    Wave-9 enrichment adds ``quantity`` (display string sourced from
    ``products.quantity_text``), ``labels_tags`` (already in DB,
    surfaced here for the 🌱 BIO badge) and ``origins_tags`` (added by
    Phase C-2 migration, surfaced for the 🇫🇷 french-origin badge).
    """
    resp = client.get("/api/v1/product/search?q=lait", headers=_auth(user))
    body = resp.json()
    assert body["items"], "fixture seeds at least one matching row"
    for item in body["items"]:
        assert "ean" in item
        assert "name" in item
        assert "brands" in item
        assert "quantity" in item
        assert "categories_tags" in item
        assert "labels_tags" in item
        assert "origins_tags" in item
        assert "source" in item


def test_search_includes_internal_source(client, user, search_corpus):
    """Both ``off`` and ``internal`` sources surface in results.

    The brief originally said "exclude source='user_suggested'", but
    that source label doesn't exist on the ``products`` table (it's a
    ``stores`` concept — see ARCH_store_resolution.md). All valid
    product sources (``off``, ``obp``, ``opf``, ``opff``, ``internal``)
    are returned. The SQL keeps a defensive ``source <> 'user_suggested'``
    clause for the day this label crosses over.
    """
    resp = client.get("/api/v1/product/search?q=lait", headers=_auth(user))
    eans = {it["ean"] for it in resp.json()["items"]}
    # The internal-source row (weighted, EAN starts with 2) is present.
    assert "2017620420006" in eans


def test_search_case_insensitive(client, user, search_corpus):
    """Mixed-case queries match all variants thanks to
    upper(unaccent(name))."""
    resp = client.get("/api/v1/product/search?q=LAIT", headers=_auth(user))
    eans = {it["ean"] for it in resp.json()["items"]}
    assert "3017620420001" in eans
    assert "3017620420002" in eans


def test_search_accent_insensitive(client, user, search_corpus):
    """Query without accent matches name with accent and vice-versa."""
    resp = client.get("/api/v1/product/search?q=lait", headers=_auth(user))
    eans = {it["ean"] for it in resp.json()["items"]}
    # "Lait demi-écrémé" (é in name) matches plain "lait"
    assert "3017620420001" in eans
    # Inverse — query with accent matches plain-ASCII name
    resp2 = client.get("/api/v1/product/search?q=laìt", headers=_auth(user))
    eans2 = {it["ean"] for it in resp2.json()["items"]}
    assert "3017620420002" in eans2  # "LAIT entier"


def test_search_matches_brand(client, user, search_corpus):
    """Query targeting only the brand (Lactel) returns the row whose
    name doesn't contain the token but brand does."""
    resp = client.get("/api/v1/product/search?q=lactel", headers=_auth(user))
    eans = {it["ean"] for it in resp.json()["items"]}
    assert "3017620420004" in eans


def test_search_ordering_prefix_before_substring(client, user, search_corpus):
    """A row whose name STARTS with the query should come before a row
    where the query is just a substring in the middle.

    "Lait" (exact prefix, 4 chars) ranks before "Crème dessert lait
    sucré" (substring) for query "lait".
    """
    resp = client.get("/api/v1/product/search?q=lait", headers=_auth(user))
    items = resp.json()["items"]
    eans = [it["ean"] for it in items]
    # The shortest exact-prefix match should appear before the long
    # substring-match row.
    idx_short = eans.index("3017620420008")  # "Lait"
    idx_long = eans.index("3017620420007")  # "Crème dessert lait sucré"
    assert idx_short < idx_long


# ── wave-9 enriched ordering (PO « pomme de terre » duplicate disambig) ─────


def test_search_enriched_fields_round_trip(client, user, potato_corpus):
    """The new quantity / origins_tags / labels_tags columns are
    returned end-to-end without mutation."""
    resp = client.get("/api/v1/product/search?q=pomme de terre", headers=_auth(user))
    assert resp.status_code == 200
    items = {it["ean"]: it for it in resp.json()["items"]}
    # Carrefour 1 kg / France
    car = items["3017620421002"]
    assert car["brands"] == "Carrefour"
    assert car["quantity"] == "1 kg"
    assert car["origins_tags"] == ["en:france"]
    # Bio Village 500 g / France / organic
    bio = items["3017620421006"]
    assert bio["quantity"] == "500 g"
    assert bio["origins_tags"] == ["en:france"]
    assert "en:organic" in (bio["labels_tags"] or [])
    # Naked row has all enrich fields NULL
    naked = items["3017620421001"]
    assert naked["brands"] is None
    assert naked["quantity"] is None
    assert naked["origins_tags"] is None
    assert naked["labels_tags"] is None


def test_search_ordering_branded_before_unbranded(client, user, potato_corpus):
    """Identical name « Pomme de terre » : branded rows surface before
    the naked OFF row (which has neither brand nor quantity)."""
    resp = client.get("/api/v1/product/search?q=pomme de terre", headers=_auth(user))
    eans = [it["ean"] for it in resp.json()["items"]]
    # Naked row 3017620421001 should be after each branded row
    idx_naked = eans.index("3017620421001")
    for branded_ean in (
        "3017620421002",  # Carrefour 1 kg / FR
        "3017620421003",  # Lidl 2 kg / BE
        "3017620421004",  # Casino
    ):
        assert eans.index(branded_ean) < idx_naked, (
            f"branded row {branded_ean} must appear before naked row, got order: {eans}"
        )


def test_search_ordering_quantified_before_unquantified(client, user, potato_corpus):
    """Among branded rows, those with a populated ``quantity_text``
    surface first — they're more discriminating for the user.

    Carrefour (1 kg) + Lidl (2 kg) before Casino (no quantity).
    """
    resp = client.get("/api/v1/product/search?q=pomme de terre", headers=_auth(user))
    eans = [it["ean"] for it in resp.json()["items"]]
    idx_casino = eans.index("3017620421004")  # branded, no quantity
    for qty_ean in ("3017620421002", "3017620421003"):
        assert eans.index(qty_ean) < idx_casino, (
            f"quantified row {qty_ean} must appear before unquantified Casino, got order: {eans}"
        )


def test_search_ordering_french_origin_before_foreign(client, user, potato_corpus):
    """Among rows with the same brand + quantity profile, the one whose
    ``origins_tags`` contains a french-origin signal surfaces first.

    Carrefour FR before Lidl BE.
    """
    resp = client.get("/api/v1/product/search?q=pomme de terre", headers=_auth(user))
    eans = [it["ean"] for it in resp.json()["items"]]
    assert eans.index("3017620421002") < eans.index("3017620421003"), (
        f"Carrefour (FR) must rank before Lidl (BE) — same name + brand + quantity bucket. Got order: {eans}"
    )


def test_search_ordering_off_source_before_internal(client, user, potato_corpus):
    """When all other criteria tie, OFF-curated rows surface before the
    internal weighted-vrac row.

    Test : the naked OFF « Pomme de terre » (no brand, no quantity, no
    origin) still ranks before the internal weighted row with the same
    name. Confirms the source-quality bucket only kicks in when the
    higher-priority buckets are tied.
    """
    resp = client.get("/api/v1/product/search?q=pomme de terre", headers=_auth(user))
    eans = [it["ean"] for it in resp.json()["items"]]
    idx_off_naked = eans.index("3017620421001")  # source=off, no brand/qty
    idx_internal = eans.index("2017620421005")  # source=internal
    assert idx_off_naked < idx_internal, (
        f"naked OFF row must rank before internal-source row at parity, got order: {eans}"
    )


# ── validation ────────────────────────────────────────────────────────────────


def test_search_query_too_long_rejected(client, user):
    long_q = "a" * 101
    resp = client.get(f"/api/v1/product/search?q={long_q}", headers=_auth(user))
    assert resp.status_code == 422


def test_search_limit_respected(client, user, search_corpus):
    resp = client.get("/api/v1/product/search?q=lait&limit=2", headers=_auth(user))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 2


def test_search_limit_capped(client, user, search_corpus):
    """Asking for more than the hard cap (50) is rejected with 422."""
    resp = client.get("/api/v1/product/search?q=lait&limit=999", headers=_auth(user))
    assert resp.status_code == 422


# ── empty / default-suggestions mode (wave 12) ─────────────────────────────────


def test_search_empty_query_returns_default_top_5(client, user, search_corpus):
    """Wave 12 (PO ticket 2026-05-14) — empty ``q`` now returns the top
    5 catalogue rows sorted alphabetically. Drives the FE AddBar's
    default-suggestions dropdown when the input is focused but empty.
    """
    resp = client.get("/api/v1/product/search?q=", headers=_auth(user))
    assert resp.status_code == 200
    items = resp.json()["items"]
    # The corpus seeds 8 rows ; we cap at 5 by default for empty q.
    assert len(items) == 5
    # Alphabetic ordering check — first item must come before subsequent
    # ones using the same unaccent+upper fold the SQL uses.
    import unicodedata as _ud

    def _norm(s: str) -> str:
        n = "".join(c for c in _ud.normalize("NFD", s) if not _ud.combining(c))
        return n.upper()

    names = [_norm(it["name"]) for it in items]
    assert names == sorted(names), f"empty-q must return alphabetic order, got: {names}"


def test_search_empty_query_response_shape(client, user, search_corpus):
    """Empty-q hits still carry the full enriched response shape so the
    FE consumer doesn't need a branch."""
    resp = client.get("/api/v1/product/search?q=", headers=_auth(user))
    items = resp.json()["items"]
    assert items, "fixture has rows so default mode must return ≥1"
    for it in items:
        for key in (
            "ean",
            "name",
            "brands",
            "quantity",
            "categories_tags",
            "labels_tags",
            "origins_tags",
            "source",
        ):
            assert key in it


def test_search_empty_query_respects_explicit_limit(client, user, search_corpus):
    """Caller can override the default 5-row cap on empty-q mode."""
    resp = client.get("/api/v1/product/search?q=&limit=3", headers=_auth(user))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3


def test_search_whitespace_only_query_treated_as_empty(client, user, search_corpus):
    """A ``q`` made only of whitespace falls into the empty-q default
    suggestions path (same as ``q=""``) — saves the FE a trim/branch."""
    resp = client.get("/api/v1/product/search?q=%20%20", headers=_auth(user))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 5


def test_search_single_char_query_now_accepted(client, user, search_corpus):
    """Wave 12 — the previous ``min_length=2`` constraint is lifted so
    the FE can debounce live results on the very first keystroke. The
    repo's normalization + LIKE pattern handles 1-char tokens fine."""
    resp = client.get("/api/v1/product/search?q=l", headers=_auth(user))
    assert resp.status_code == 200
    # All "Lait*" rows + "Crème dessert lait*" match "L".
    eans = {it["ean"] for it in resp.json()["items"]}
    assert "3017620420008" in eans  # "Lait"


# ── auth ──────────────────────────────────────────────────────────────────────


def test_search_no_token_returns_401(client, search_corpus):
    resp = client.get("/api/v1/product/search?q=lait")
    assert resp.status_code == 401
