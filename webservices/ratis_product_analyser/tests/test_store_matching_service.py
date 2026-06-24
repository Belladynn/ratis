"""
TDD tests for services/store_matching_service (DA-35).

Covers:
  - extract_postal_code / extract_city_hint
  - match_retailer_from_header: exact alias, pg_trgm fuzzy, no match, threshold
  - match_store_from_address: 1 match, disambiguation via city, no match,
    city-only fallback
  - cache_retailer_header_resolution: insert + increment of seen_count
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from ratis_core.models.retailer import Retailer, RetailerAlias
from ratis_core.models.store import Store
from services.store_matching_service import (
    cache_retailer_header_resolution,
    extract_city_hint,
    extract_postal_code,
    match_retailer_from_header,
    match_store_from_address,
)
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def retailer_carrefour(db) -> Retailer:
    r = Retailer(
        id=uuid.uuid4(),
        canonical_name="Carrefour",
        slug="carrefour",
        country_code="FR",
        is_verified=True,
    )
    db.add(r)
    db.flush()
    for alias in ("carrefour", "crf", "carref", "carrefour hyper"):
        db.add(RetailerAlias(retailer_id=r.id, alias=alias, source="manual"))
    db.flush()
    db.commit()
    return r


@pytest.fixture
def retailer_lidl(db) -> Retailer:
    r = Retailer(
        id=uuid.uuid4(),
        canonical_name="Lidl",
        slug="lidl",
        country_code="FR",
        is_verified=True,
    )
    db.add(r)
    db.flush()
    for alias in ("lidl", "lidl france"):
        db.add(RetailerAlias(retailer_id=r.id, alias=alias, source="manual"))
    db.flush()
    db.commit()
    return r


def _mk_store(
    db,
    *,
    retailer_id: uuid.UUID,
    postal_code: str,
    city: str,
    name: str | None = None,
    lat: float = 48.85,
    lng: float = 2.35,
) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name or f"Store {postal_code}",
        retailer_id=retailer_id,
        postal_code=postal_code,
        city=city,
        address=f"1 rue Test, {postal_code} {city}",
        lat=Decimal(str(lat)),
        lng=Decimal(str(lng)),
        is_disabled=False,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


# ---------------------------------------------------------------------------
# extract_postal_code / extract_city_hint
# ---------------------------------------------------------------------------


class TestExtractPostalCode:
    def test_single_postal(self):
        assert extract_postal_code("CARREFOUR 75011 PARIS") == "75011"

    def test_postal_embedded_in_words(self):
        # no word boundary → no match
        assert extract_postal_code("abc12345def") is None

    def test_no_postal(self):
        assert extract_postal_code("just some text") is None

    def test_first_of_multiple(self):
        assert extract_postal_code("ref 12345 - 67890") == "12345"

    def test_empty_string(self):
        assert extract_postal_code("") is None

    def test_ignores_4_or_6_digit_numbers(self):
        assert extract_postal_code("tel 01234567 total 1234") is None


class TestExtractCityHint:
    def test_city_after_postal(self):
        assert extract_city_hint("CARREFOUR 75011 PARIS", "75011") == "PARIS"

    def test_city_after_postal_composite(self):
        # Only the first token after the postal is returned.
        assert extract_city_hint("... 75011 Paris 11e ...", "75011") == "Paris"

    def test_no_postal_returns_none(self):
        assert extract_city_hint("Just some text", None) is None

    def test_postal_not_found_in_text(self):
        assert extract_city_hint("nothing relevant here", "75011") is None

    def test_postal_at_end_of_text(self):
        assert extract_city_hint("long text 75011", "75011") is None


# ---------------------------------------------------------------------------
# match_retailer_from_header
# ---------------------------------------------------------------------------


class TestMatchRetailerFromHeader:
    def test_exact_alias_returns_max_confidence(self, db, retailer_carrefour):
        match = match_retailer_from_header(db, "CARREFOUR")
        assert match is not None
        assert match.retailer_id == retailer_carrefour.id
        assert match.confidence == pytest.approx(1.0)
        assert match.canonical_name == "Carrefour"
        assert match.matched_alias == "carrefour"

    def test_exact_alias_case_insensitive(self, db, retailer_carrefour):
        match = match_retailer_from_header(db, "CaRrEfOuR")
        assert match is not None
        assert match.retailer_id == retailer_carrefour.id

    def test_exact_alias_trimmed(self, db, retailer_carrefour):
        match = match_retailer_from_header(db, "  CARREFOUR  ")
        assert match is not None

    def test_fuzzy_match_above_threshold(self, db, retailer_carrefour):
        # "carrefour" vs "carrefou" — typo, strong similarity
        match = match_retailer_from_header(db, "CARREFOU", min_similarity=0.6)
        assert match is not None
        assert match.retailer_id == retailer_carrefour.id
        assert 0.6 <= match.confidence < 1.0

    def test_fuzzy_no_match_below_threshold(self, db, retailer_carrefour):
        # totally unrelated string
        match = match_retailer_from_header(db, "ZZZZZZZ", min_similarity=0.75)
        assert match is None

    def test_empty_header_returns_none(self, db, retailer_carrefour):
        assert match_retailer_from_header(db, "") is None
        assert match_retailer_from_header(db, "   ") is None

    def test_no_retailer_in_db_returns_none(self, db):
        assert match_retailer_from_header(db, "carrefour") is None

    def test_accents_are_stripped(self, db, retailer_lidl):
        # Retailer alias is "lidl"; OCR header with accents should still match.
        match = match_retailer_from_header(db, "LÏDL")
        assert match is not None
        assert match.retailer_id == retailer_lidl.id

    def test_prefers_highest_confidence_alias(self, db, retailer_carrefour, retailer_lidl):
        # Input matches Carrefour exactly, should pick that one over Lidl.
        match = match_retailer_from_header(db, "CARREFOUR")
        assert match is not None
        assert match.retailer_id == retailer_carrefour.id


# ---------------------------------------------------------------------------
# match_store_from_address
# ---------------------------------------------------------------------------


class TestMatchStoreFromAddress:
    def test_postal_code_unique_match(self, db, retailer_carrefour):
        store = _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="75011", city="Paris")
        match = match_store_from_address(db, retailer_carrefour.id, "75011")
        assert match is not None
        assert match.store_id == store.id
        assert match.is_ambiguous is False
        assert match.confidence == pytest.approx(1.0)

    def test_multiple_stores_city_hint_disambiguates(self, db, retailer_carrefour):
        s_a = _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="75011", city="Paris", name="store A")
        _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="75011", city="Lyon", name="store B")
        match = match_store_from_address(db, retailer_carrefour.id, "75011", city_hint="Paris")
        assert match is not None
        assert match.store_id == s_a.id
        assert match.is_ambiguous is False

    def test_multiple_stores_no_hint_is_ambiguous(self, db, retailer_carrefour):
        _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="75011", city="Paris", name="a")
        _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="75011", city="Paris", name="b")
        match = match_store_from_address(db, retailer_carrefour.id, "75011")
        assert match is not None
        assert match.is_ambiguous is True
        assert match.confidence < 1.0

    def test_no_postal_city_only_fallback(self, db, retailer_carrefour):
        store = _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="69001", city="Lyon")
        match = match_store_from_address(db, retailer_carrefour.id, postal_code=None, city_hint="Lyon")
        assert match is not None
        assert match.store_id == store.id
        # Lower confidence on city-only fallback.
        assert match.confidence < 1.0

    def test_nothing_matches_returns_none(self, db, retailer_carrefour):
        _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="69001", city="Lyon")
        match = match_store_from_address(db, retailer_carrefour.id, postal_code="99999", city_hint="Nowhere")
        assert match is None

    def test_ignores_disabled_stores(self, db, retailer_carrefour):
        s = _mk_store(db, retailer_id=retailer_carrefour.id, postal_code="75011", city="Paris")
        db.execute(
            # PG ``disabled_at_check`` : keep ``is_disabled`` and
            # ``disabled_at`` coherent in the same UPDATE.
            text("UPDATE stores SET is_disabled = true, disabled_at = now() WHERE id = :id"),
            {"id": str(s.id)},
        )
        db.commit()
        match = match_store_from_address(db, retailer_carrefour.id, "75011")
        assert match is None

    def test_ignores_other_retailers(self, db, retailer_carrefour, retailer_lidl):
        _mk_store(db, retailer_id=retailer_lidl.id, postal_code="75011", city="Paris")
        match = match_store_from_address(db, retailer_carrefour.id, "75011")
        assert match is None


# ---------------------------------------------------------------------------
# cache_retailer_header_resolution
# ---------------------------------------------------------------------------


class TestCacheRetailerHeaderResolution:
    def test_first_write_inserts_row(self, db, retailer_carrefour):
        from services.store_matching_service import RetailerMatch

        match = RetailerMatch(
            retailer_id=retailer_carrefour.id,
            canonical_name="Carrefour",
            confidence=1.0,
            matched_alias="carrefour",
        )
        cache_retailer_header_resolution(db, "CARREFOUR MARKET", match)
        db.commit()

        row = db.execute(
            text(
                "SELECT raw_ocr, corrected, entity_id, seen_count, type, source "
                "FROM ocr_knowledge WHERE raw_ocr = :raw AND type = 'retailer_header'"
            ),
            {"raw": "CARREFOUR MARKET"},
        ).first()
        assert row is not None
        assert row[0] == "CARREFOUR MARKET"
        assert row[1] == "Carrefour"
        assert row[2] == retailer_carrefour.id
        assert row[3] == 1
        assert row[4] == "retailer_header"
        assert row[5] == "ocr_arbitrage"

    def test_second_write_increments_seen_count(self, db, retailer_carrefour):
        from services.store_matching_service import RetailerMatch

        match = RetailerMatch(
            retailer_id=retailer_carrefour.id,
            canonical_name="Carrefour",
            confidence=1.0,
            matched_alias="carrefour",
        )
        cache_retailer_header_resolution(db, "CRF HYPER", match)
        cache_retailer_header_resolution(db, "CRF HYPER", match)
        db.commit()

        row = db.execute(
            text("SELECT seen_count FROM ocr_knowledge WHERE raw_ocr = :raw AND type = 'retailer_header'"),
            {"raw": "CRF HYPER"},
        ).first()
        assert row is not None
        assert row[0] == 2
