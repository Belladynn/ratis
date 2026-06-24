"""Tests for off_sync.extractor — no DB, no HTTP."""

import json
from pathlib import Path

import pytest
from off_sync.extractor import _derive_storage_type, _safe_url, extract_net_weight, extract_product, is_france_product

_FIXTURES = Path(__file__).parent / "fixtures"

_OFF_IMG = "https://images.openfoodfacts.org/images/products/301/762/042/2003/front_fr.jpg"
_OFF_IMG_SMALL = "https://images.openfoodfacts.org/images/products/301/762/042/2003/front_fr.200.jpg"

# Minimal rules dict — substring patterns (no $), no file I/O.
_RULES = {
    "frozen": ["frozen", "surgel", "congel"],
    "fresh": ["fresh", "frais", "réfrigér"],
}

# Rules dict with $ word-boundary patterns — mirrors product_knowledge.json.
_RULES_D = {
    "frozen": ["frozen$", "surgel", "congel", "tiefkühl"],
    "fresh": ["frais$", "fresh$", "réfrigér", "kühlpflichtig"],
}


# ── extract_product ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_name",
    [
        ({"code": "3017620422003", "product_name_fr": "Nutella"}, "Nutella"),
        ({"code": "12345678", "product_name": "Generic"}, "Generic"),  # 8-digit EAN
        ({"code": "3017620422003", "product_name_fr": " Nutella "}, "Nutella"),  # stripped
    ],
)
def test_extract_product_valid(raw, expected_name):
    result = extract_product(raw)
    assert result is not None
    assert result["name"] == expected_name


@pytest.mark.parametrize(
    "raw",
    [
        {"code": "abc123", "product_name_fr": "Name"},  # non-numeric EAN
        {"code": "123456", "product_name_fr": "Name"},  # 6 digits — too short
        {"code": "3017620422003123456", "product_name_fr": "N"},  # 19 digits — too long
        {"code": "3017620422003", "product_name_fr": ""},  # empty name
        {"code": "3017620422003", "product_name_fr": "   "},  # whitespace-only name
        {"code": "3017620422003"},  # no name field
        {"code": "", "product_name_fr": "Name"},  # empty code
        {},  # empty raw
    ],
)
def test_extract_product_invalid(raw):
    assert extract_product(raw) is None


def test_extract_product_prefers_fr_name():
    raw = {"code": "3017620422003", "product_name_fr": "FR", "product_name": "EN"}
    assert extract_product(raw)["name"] == "FR"


def test_extract_product_falls_back_to_product_name():
    raw = {"code": "3017620422003", "product_name_fr": "", "product_name": "EN"}
    assert extract_product(raw)["name"] == "EN"


def test_extract_product_null_photo():
    raw = {"code": "3017620422003", "product_name_fr": "Nutella"}
    assert extract_product(raw)["photo_url"] is None


def test_extract_product_photo_present():
    raw = {"code": "3017620422003", "product_name_fr": "Nutella", "image_front_url": _OFF_IMG}
    assert extract_product(raw)["photo_url"] == _OFF_IMG


def test_extract_product_returns_expected_keys():
    """Ensure the dict shape is stable — catches accidental key renames."""
    raw = {"code": "3017620422003", "product_name_fr": "Nutella", "image_front_url": _OFF_IMG}
    result = extract_product(raw)
    assert set(result.keys()) == {
        "ean",
        "name",
        "photo_url",
        "photo_url_small",
        "brands",
        "product_quantity",
        "product_quantity_unit",
        "quantity_raw",
        "storage_type",
        "allergens_tags",
        "ingredients_tags",
        "categories_tags",
        "labels_tags",
        # Phase C-2 — origins_tags feeds the ``attribute:french`` qualifier emit.
        "origins_tags",
        # OFF multi-field enrichment (PR feat/off-sync-multi-fields).
        "product_name_fr",
        "generic_name_fr",
        "brands_text",
        "quantity_text",
    }


# ── multi-field enrichment ────────────────────────────────────────────────────


def test_extract_product_populates_multi_fields():
    """All OFF name-related fields are persisted verbatim."""
    raw = {
        "code": "7610113013175",
        "product_name_fr": "Hipro + protéines fraise",
        "product_name": "Hipro + strawberry protein drink",
        "generic_name_fr": "Yaourt à boire saveur fraise enrichi en protéines",
        "brands": "Hipro,Danone",
        "quantity": "4 x 250 g",
    }
    result = extract_product(raw)
    assert result["product_name_fr"] == "Hipro + protéines fraise"
    assert result["generic_name_fr"] == "Yaourt à boire saveur fraise enrichi en protéines"
    assert result["brands_text"] == "Hipro,Danone"
    assert result["quantity_text"] == "4 x 250 g"
    # ``name`` keeps using the existing best-of-FR-then-EN logic — the
    # international ``product_name`` is not persisted as a separate column.
    assert result["name"] == "Hipro + protéines fraise"


def test_extract_product_multi_fields_default_to_none():
    """Missing OFF fields are persisted as NULL — no fabricated defaults."""
    raw = {"code": "3017620422003", "product_name_fr": "Nutella"}
    result = extract_product(raw)
    assert result["product_name_fr"] == "Nutella"
    assert result["generic_name_fr"] is None
    assert result["brands_text"] is None
    assert result["quantity_text"] is None


def test_extract_product_falls_back_to_generic_name_when_fr_missing():
    """When only ``generic_name`` (international) is set, it populates generic_name_fr."""
    raw = {
        "code": "3017620422003",
        "product_name_fr": "Nutella",
        "generic_name": "Hazelnut spread",
    }
    result = extract_product(raw)
    assert result["generic_name_fr"] == "Hazelnut spread"


def test_extract_product_strips_whitespace_in_multi_fields():
    raw = {
        "code": "3017620422003",
        "product_name_fr": "  Nutella  ",
        "generic_name_fr": "  Pâte à tartiner  ",
    }
    result = extract_product(raw)
    assert result["product_name_fr"] == "Nutella"
    assert result["generic_name_fr"] == "Pâte à tartiner"


def test_extract_product_drops_oversized_generic_name():
    """Oversized generic_name_fr is dropped to NULL — same as oversized brands/name."""
    raw = {
        "code": "3017620422003",
        "product_name_fr": "Nutella",
        "generic_name_fr": "x" * 600,  # > _MAX_GENERIC_NAME (500)
    }
    result = extract_product(raw)
    assert result["generic_name_fr"] is None


# ── _safe_url — photo URL whitelist ───────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://images.openfoodfacts.org/images/products/foo.jpg",
        "https://static.openfoodfacts.org/images/misc/openfoodfacts-logo.png",
        "https://world.openfoodfacts.org/images/products/bar.jpg",
    ],
)
def test_safe_url_allowed_hosts(url):
    assert _safe_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/tracker.jpg",
        "http://attacker.example/xss.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,abc",
        "https://images.openfoodfacts.org.evil.com/x.jpg",  # subdomain spoofing
        "https://evil.com/https://images.openfoodfacts.org/x.jpg",  # path confusion
    ],
)
def test_safe_url_rejected_hosts(url):
    assert _safe_url(url) is None


def test_safe_url_none():
    assert _safe_url(None) is None


def test_safe_url_too_long():
    url = "https://images.openfoodfacts.org/" + "a" * 2048
    assert _safe_url(url) is None


def test_safe_url_accepts_obp_cdn():
    url = "https://images.openbeautyfacts.org/images/products/123/front.jpg"
    assert _safe_url(url) == url


def test_safe_url_accepts_opf_cdn():
    url = "https://images.openproductsfacts.org/images/products/123/front.jpg"
    assert _safe_url(url) == url


def test_safe_url_accepts_opff_cdn():
    url = "https://images.openpetfoodfacts.org/images/products/123/front.jpg"
    assert _safe_url(url) == url


def test_safe_url_rejects_unknown_host_after_widening():
    """Regression : widening must not let arbitrary hosts through."""
    assert _safe_url("https://evil.example.com/img.jpg") is None


def test_extract_product_photo_untrusted_url_discarded():
    """A compromised OFF API serving a foreign URL must be silently dropped."""
    raw = {"code": "3017620422003", "product_name_fr": "X", "image_front_url": "https://evil.com/track.jpg"}
    assert extract_product(raw)["photo_url"] is None


# ── field length caps ─────────────────────────────────────────────────────────


def test_extract_product_name_too_long_rejected():
    raw = {"code": "3017620422003", "product_name_fr": "A" * 501}
    assert extract_product(raw) is None


def test_extract_product_brands_too_long_nulled():
    raw = {"code": "3017620422003", "product_name_fr": "X", "brands": "B" * 201}
    result = extract_product(raw)
    assert result is not None
    assert result["brands"] is None


def test_extract_product_qty_raw_too_long_nulled():
    raw = {"code": "3017620422003", "product_name_fr": "X", "quantity": "Q" * 101}
    result = extract_product(raw)
    assert result is not None
    assert result["quantity_raw"] is None


# ── brands / photo_url_small ──────────────────────────────────────────────────


def test_extract_product_brands_present():
    raw = {"code": "3017620422003", "product_name_fr": "X", "brands": "Ferrero"}
    assert extract_product(raw)["brands"] == "Ferrero"


def test_extract_product_brands_stripped():
    raw = {"code": "3017620422003", "product_name_fr": "X", "brands": "  Ferrero  "}
    assert extract_product(raw)["brands"] == "Ferrero"


def test_extract_product_brands_absent():
    raw = {"code": "3017620422003", "product_name_fr": "X"}
    assert extract_product(raw)["brands"] is None


def test_extract_product_photo_url_small():
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "image_front_small_url": _OFF_IMG_SMALL,
    }
    assert extract_product(raw)["photo_url_small"] == _OFF_IMG_SMALL


def test_extract_product_photo_url_small_absent():
    raw = {"code": "3017620422003", "product_name_fr": "X"}
    assert extract_product(raw)["photo_url_small"] is None


# ── quantity parsing (via extract_product) ─────────────────────────────────────


@pytest.mark.parametrize(
    "qty_str,expected_qty,expected_unit",
    [
        ("500 g", 500.0, "g"),
        ("1.5 kg", 1.5, "kg"),
        ("33 cl", 33.0, "cl"),
        ("250mL", 250.0, "mL"),
        ("1,5 L", 1.5, "L"),
        ("6 x 125 g", 750.0, "g"),  # multi-pack
        ("4 x 250 mL", 1000.0, "mL"),  # multi-pack
        ("6X100g", 600.0, "g"),  # no spaces
    ],
)
def test_extract_product_parses_quantity(qty_str, expected_qty, expected_unit):
    raw = {"code": "3017620422003", "product_name_fr": "X", "quantity": qty_str}
    result = extract_product(raw)
    assert result["product_quantity"] == expected_qty
    assert result["product_quantity_unit"] == expected_unit
    assert result["quantity_raw"] == qty_str


@pytest.mark.parametrize("qty_str", ["1 pièce", "assorted"])
def test_extract_product_unparseable_quantity_stored_raw(qty_str):
    raw = {"code": "3017620422003", "product_name_fr": "X", "quantity": qty_str}
    result = extract_product(raw)
    assert result["product_quantity"] is None
    assert result["product_quantity_unit"] is None
    assert result["quantity_raw"] == qty_str


def test_extract_product_no_quantity():
    raw = {"code": "3017620422003", "product_name_fr": "X"}
    result = extract_product(raw)
    assert result["product_quantity"] is None
    assert result["product_quantity_unit"] is None
    assert result["quantity_raw"] is None


# ── storage_type — _derive_storage_type unit tests (injected rules, no file I/O) ──


def test_storage_type_frozen_from_categories():
    assert _derive_storage_type(["en:frozen-foods"], [], None, rules=_RULES) == "frozen"


def test_storage_type_frozen_french_tag():
    assert _derive_storage_type(["fr:surgeles"], [], None, rules=_RULES) == "frozen"


def test_storage_type_frozen_partial_match():
    """'congel' matches 'fr:produits-a-congeler'."""
    assert _derive_storage_type(["fr:produits-a-congeler"], [], None, rules=_RULES) == "frozen"


def test_storage_type_fresh_from_categories():
    assert _derive_storage_type(["en:fresh-meats"], [], None, rules=_RULES) == "fresh"


def test_storage_type_fresh_french_tag():
    assert _derive_storage_type(["fr:produits-frais"], [], None, rules=_RULES) == "fresh"


def test_storage_type_fresh_from_conservation_conditions():
    """conservation_conditions free-text triggers fresh."""
    assert _derive_storage_type([], [], "À conserver réfrigéré", rules=_RULES) == "fresh"


def test_storage_type_fresh_from_labels_only():
    """Pattern found in labels_tags only — still detected."""
    assert _derive_storage_type([], ["en:frozen-ready-meals"], None, rules=_RULES) == "frozen"


def test_storage_type_ambient_known_food_category():
    """categories_tags present, no frozen/fresh pattern → ambient."""
    assert _derive_storage_type(["en:beverages", "en:sodas"], [], None, rules=_RULES) == "ambient"


def test_storage_type_unmatched_no_categories():
    """labels/conservation present, no categories, no pattern → unmatched."""
    assert _derive_storage_type([], ["en:organic"], "Conserver à l'abri de la lumière", rules=_RULES) == "unmatched"


def test_storage_type_none_no_fields():
    """No fields at all → None."""
    assert _derive_storage_type([], [], None, rules=_RULES) is None


def test_storage_type_frozen_beats_fresh():
    """frozen patterns checked before fresh."""
    assert _derive_storage_type(["en:frozen-foods", "en:fresh-foods"], [], None, rules=_RULES) == "frozen"


# ── storage_type — $ word-boundary patterns ────────────────────────────────────


def test_dollar_fresh_exact_token_produits_frais():
    """frais$ matches token 'frais' in 'fr:produits-frais'."""
    assert _derive_storage_type(["fr:produits-frais"], [], None, rules=_RULES_D) == "fresh"


def test_dollar_no_false_positive_confitures_fraises():
    """frais$ must NOT match 'fraises' token in 'fr:confitures-de-fraises'."""
    assert _derive_storage_type(["fr:confitures-de-fraises"], [], None, rules=_RULES_D) == "ambient"


def test_dollar_fresh_exact_token_fresh_foods():
    """fresh$ matches token 'fresh' in 'en:fresh-foods'."""
    assert _derive_storage_type(["en:fresh-foods"], [], None, rules=_RULES_D) == "fresh"


def test_dollar_frozen_exact_token():
    """frozen$ matches token 'frozen' in 'en:frozen-foods'."""
    assert _derive_storage_type(["en:frozen-foods"], [], None, rules=_RULES_D) == "frozen"


def test_dollar_substring_still_works_for_non_dollar_pattern():
    """Non-$ pattern still uses substring — 'surgel' in 'fr:surgeles'."""
    assert _derive_storage_type(["fr:surgeles"], [], None, rules=_RULES_D) == "frozen"


def test_dollar_conservation_word_boundary_matches():
    """frais$ matches standalone word 'frais' in conservation_conditions."""
    assert _derive_storage_type([], [], "À conserver frais", rules=_RULES_D) == "fresh"


def test_dollar_conservation_no_false_positive_fraises():
    """frais$ must NOT match 'fraises' in free text — not a standalone word."""
    assert _derive_storage_type([], [], "Parfum fraises", rules=_RULES_D) == "unmatched"


# ── storage_type via extract_product (uses module-level rules file) ────────────


@pytest.mark.parametrize(
    "categories,expected_type",
    [
        (["en:frozen-foods", "en:snacks"], "frozen"),
        (["en:fresh-meats"], "fresh"),
        (["en:dairy-products", "en:fresh-dairy-products"], "fresh"),
        (["en:beverages", "en:sodas"], "ambient"),
        ([], None),
    ],
)
def test_extract_product_storage_type(categories, expected_type):
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "categories_tags": categories,
    }
    assert extract_product(raw)["storage_type"] == expected_type


# ── allergens / ingredients ────────────────────────────────────────────────────


def test_extract_product_allergens_and_ingredients():
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "allergens_tags": ["en:gluten", "en:milk"],
        "ingredients_tags": ["en:sugar", "en:cocoa"],
    }
    result = extract_product(raw)
    assert result["allergens_tags"] == ["en:gluten", "en:milk"]
    assert result["ingredients_tags"] == ["en:sugar", "en:cocoa"]


def test_extract_product_missing_tags_defaults_to_empty_list():
    raw = {"code": "3017620422003", "product_name_fr": "X"}
    result = extract_product(raw)
    assert result["allergens_tags"] == []
    assert result["ingredients_tags"] == []
    assert result["labels_tags"] == []


def test_extract_product_labels_tags():
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "labels_tags": ["en:organic", "en:fair-trade"],
    }
    assert extract_product(raw)["labels_tags"] == ["en:organic", "en:fair-trade"]


# ── Phase C-2 — origins_tags ────────────────────────────────────────────


def test_extract_product_origins_tags_populated():
    """OFF ``origins_tags`` is persisted verbatim — drives the
    ``attribute:french`` mission qualifier emit (Phase C-2)."""
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "origins_tags": ["en:france", "en:european-union"],
    }
    assert extract_product(raw)["origins_tags"] == [
        "en:france",
        "en:european-union",
    ]


def test_extract_product_origins_tags_missing_defaults_to_empty():
    """OFF row without ``origins_tags`` → empty list. Same convention as
    the other tag arrays — the consumer (``is_french_product``) treats
    None/empty identically as "no signal"."""
    raw = {"code": "3017620422003", "product_name_fr": "X"}
    assert extract_product(raw)["origins_tags"] == []


def test_extract_product_origins_tags_sanitises_oversized_items():
    """Items longer than ``_MAX_TAG_ITEM`` are dropped — defence against
    a compromised OFF payload (same rule as ``_sanitize_tags`` everywhere)."""
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "origins_tags": ["en:france", "x" * 200],
    }
    assert extract_product(raw)["origins_tags"] == ["en:france"]


def test_extract_product_origins_tags_drops_non_string_items():
    """Non-string elements (None, ints, dicts) are filtered out by
    ``_sanitize_tags`` — keeps the contract array-of-text clean."""
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "origins_tags": ["en:france", None, 42, {"foo": "bar"}],
    }
    assert extract_product(raw)["origins_tags"] == ["en:france"]


def test_extract_product_origins_tags_multi_french_signals():
    """Real OFF data : the array may carry the english + french prefixes
    (``en:france`` + ``fr:france``) alongside broader origin tags. All
    survive untouched — the matching is the consumer's responsibility."""
    raw = {
        "code": "3017620422003",
        "product_name_fr": "X",
        "origins_tags": [
            "en:france",
            "fr:france",
            "en:european-union",
        ],
    }
    assert extract_product(raw)["origins_tags"] == [
        "en:france",
        "fr:france",
        "en:european-union",
    ]


# ── extract_net_weight ────────────────────────────────────────────────────────


def test_net_weight_p1_numeric_fields():
    """P1: product_quantity (numeric) + product_quantity_unit."""
    assert extract_net_weight({"product_quantity": 400, "product_quantity_unit": "g"}) == (400.0, "g")


def test_net_weight_p1_float():
    assert extract_net_weight({"product_quantity": 1.5, "product_quantity_unit": "kg"}) == (1.5, "kg")


def test_net_weight_p1_missing_unit_falls_through():
    """P1 skipped when unit is absent — falls through to P2/P3."""
    result = extract_net_weight({"product_quantity": 400})
    assert result is None  # no packagings, no quantity string either


def test_net_weight_p2_packagings():
    """P2: first packaging component with value × n_units."""
    product = {
        "packagings": [
            {"quantity_per_unit_value": 125, "number_of_units": 6, "quantity_per_unit_unit": "g"},
            {"quantity_per_unit_value": 125, "number_of_units": 6, "quantity_per_unit_unit": "g"},
        ]
    }
    assert extract_net_weight(product) == (750.0, "g")


def test_net_weight_p2_default_unit_g():
    """P2 defaults to 'g' when quantity_per_unit_unit is absent."""
    product = {"packagings": [{"quantity_per_unit_value": 125, "number_of_units": 6}]}
    assert extract_net_weight(product) == (750.0, "g")


def test_net_weight_p2_skips_incomplete_components():
    """P2 skips components missing value or n_units, takes first valid one."""
    product = {
        "packagings": [
            {"number_of_units": 6},  # no value
            {"quantity_per_unit_value": 200, "number_of_units": 3, "quantity_per_unit_unit": "mL"},
        ]
    }
    assert extract_net_weight(product) == (600.0, "mL")


def test_net_weight_p2_beats_p3():
    """P2 has higher priority than P3 — packagings used even when quantity string present."""
    product = {
        "packagings": [{"quantity_per_unit_value": 100, "number_of_units": 4, "quantity_per_unit_unit": "g"}],
        "quantity": "500 g",
    }
    assert extract_net_weight(product) == (400.0, "g")


def test_net_weight_p3_simple_string():
    """P3: simple quantity string."""
    assert extract_net_weight({"quantity": "500 g"}) == (500.0, "g")


def test_net_weight_p3_multi_pack_string():
    """P3: multi-pack string '6 x 125 g'."""
    assert extract_net_weight({"quantity": "6 x 125 g"}) == (750.0, "g")


def test_net_weight_p3_quantity_raw_field():
    """P3: also reads quantity_raw (already-extracted product dict)."""
    assert extract_net_weight({"quantity_raw": "250 mL"}) == (250.0, "mL")


def test_net_weight_no_data():
    """None when no usable data."""
    assert extract_net_weight({"product_name": "Produit inconnu"}) is None


def test_net_weight_empty_dict():
    assert extract_net_weight({}) is None


# ── is_france_product ─────────────────────────────────────────────────────────


def test_is_france_product_true():
    raw = {"countries_tags": ["en:france", "en:belgium"]}
    assert is_france_product(raw) is True


def test_is_france_product_false():
    raw = {"countries_tags": ["en:germany", "en:spain"]}
    assert is_france_product(raw) is False


def test_is_france_product_missing_field():
    assert is_france_product({}) is False


def test_is_france_product_empty_list():
    assert is_france_product({"countries_tags": []}) is False


# ── product_knowledge.json contract ───────────────────────────────────────────


def test_product_knowledge_storage_type_contract():
    """product_knowledge.json must expose a 'storage_type' section with frozen+fresh keys."""
    from ratis_core.knowledge import load_knowledge

    data = load_knowledge()
    assert "storage_type" in data, "Missing 'storage_type' section"
    assert "frozen" in data["storage_type"], "Missing 'frozen' key in storage_type"
    assert "fresh" in data["storage_type"], "Missing 'fresh' key in storage_type"
    assert all(isinstance(v, list) for v in data["storage_type"].values()), "All storage_type values must be lists"


# ── OBP regression (multi-source ingestion) ───────────────────────────────────


def test_extract_product_obp_sample():
    """Regression : a real OBP API response extracts cleanly with the right source.

    OBP shares the OFF schema verbatim (Q1 of the spec). ``extract_product``
    works on OBP cosmetics rows when passed ``source=Source.OBP``. The fixture
    is an anonymised sample captured via the ``cgi/search.pl`` endpoint
    (countries_tags_en=france) ; PII contributor metadata stripped.

    ``storage_type`` MUST be ``None`` for cosmetics : the storage-type
    classifier targets food categories (frozen/fresh/ambient) which are
    meaningless for cosmetics. ``Source.classify_storage=False`` skips the
    derivation entirely — cf plan PR2 § DA-08 ("storage_type toujours NULL").

    The photo, if present, must point to an OBP-allowed CDN host (security : a
    foreign URL would be rejected by ``_safe_url`` — defence-in-depth).
    """
    from off_sync.sources import get_source

    raw = json.loads((_FIXTURES / "obp_sample.json").read_text())
    p = extract_product(raw, source=get_source("obp"))
    assert p is not None
    assert p["ean"]  # non-empty
    assert p["name"]  # non-empty
    # storage_type classification is OFF-only — OBP rows always store NULL.
    assert p["storage_type"] is None
    if p["photo_url"]:
        assert "openbeautyfacts.org" in p["photo_url"]
    if p["photo_url_small"]:
        assert "openbeautyfacts.org" in p["photo_url_small"]


def test_extract_product_obp_back_compat_no_source():
    """Back-compat : extract_product without ``source`` keeps legacy classifier.

    Pre-PR2 callers (and the test corpus that doesn't pass ``source``) must
    still get a ``storage_type`` derived from categories — switching the
    default would silently regress every OFF row's storage_type to NULL.
    """
    raw = json.loads((_FIXTURES / "obp_sample.json").read_text())
    p = extract_product(raw)  # no source kwarg → legacy behaviour
    assert p is not None
    # OBP cosmetics carry categories → legacy fallback yields 'ambient' or 'unmatched'.
    # We don't assert which — we only assert it's NOT None (back-compat unchanged).
    assert p["storage_type"] is not None


def test_extract_product_off_source_keeps_classification():
    """OFF source must keep storage_type classification active.

    Synthetic frozen OFF item — categories include "Surgelés", classify
    via product_knowledge.json food rules → ``frozen``.
    """
    from off_sync.sources import get_source

    raw = {
        "code": "3017620422003",
        "product_name_fr": "Bâtonnets de poisson surgelés",
        "categories_tags": ["en:frozen-foods", "en:fish"],
    }
    p = extract_product(raw, source=get_source("off"))
    assert p is not None
    # Whatever the rules decide, it must NOT be None for an OFF item with
    # categories — that would mean the flag accidentally disabled classify.
    assert p["storage_type"] is not None
