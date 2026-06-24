"""Tests for ``batch_shared.store_consolidation``.

Three layers :

1. **Pure-logic tests** (no DB) — ``trust_priority()`` lookup table.
2. **find_match() DB tests** — by SIRET, by osm_id, by fuzzy (radius +
   retailer + name trgm).
3. **apply_upsert() DB tests** — INSERT / UPDATE-in-place / merge across
   sources / preserve higher-trust / conflict log.

Test DB fixtures (``engine`` / ``db``) come from conftest.py — SAVEPOINT
isolation, fresh schema per session.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from batch_shared.store_consolidation import (
    CandidateStore,
    TrustPriority,
    UpsertResult,
    apply_upsert,
    find_match,
    trust_priority,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

# ============================================================
# Helpers — seed minimal rows directly via raw SQL so the test
# stays decoupled from the OSM batch's upsert_store() helper.
# ============================================================


def _seed_retailer(db: Session, name: str = "Carrefour", slug: str = "carrefour") -> uuid.UUID:
    """INSERT a retailer row and return its UUID."""
    row = db.execute(
        text(
            """
            INSERT INTO retailers (canonical_name, slug, is_verified)
            VALUES (:name, :slug, true)
            RETURNING id
            """
        ),
        {"name": name, "slug": slug},
    ).first()
    return row.id


def _seed_store(
    db: Session,
    *,
    name: str = "Carrefour Market Bastille",
    source: str = "osm",
    lat: Decimal = Decimal("48.853000"),
    lng: Decimal = Decimal("2.369000"),
    retailer_id: uuid.UUID | None = None,
    siret: str | None = None,
    osm_id: int | None = None,
    address: str | None = "12 Rue de la Roquette",
    city: str | None = "Paris",
    postal_code: str | None = "75011",
) -> uuid.UUID:
    """INSERT a stores row via raw SQL, return its UUID."""
    row = db.execute(
        text(
            """
            INSERT INTO stores (
                name, retailer_id, address, city, postal_code,
                lat, lng, siret, osm_id, source, is_disabled
            ) VALUES (
                :name, :retailer_id, :address, :city, :postal_code,
                :lat, :lng, :siret, :osm_id, :source, false
            )
            RETURNING id
            """
        ),
        {
            "name": name,
            "retailer_id": retailer_id,
            "address": address,
            "city": city,
            "postal_code": postal_code,
            "lat": lat,
            "lng": lng,
            "siret": siret,
            "osm_id": osm_id,
            "source": source,
        },
    ).first()
    return row.id


# ============================================================
# trust_priority — pure logic
# ============================================================


def test_trust_priority_lookup_order():
    """admin > sirene > overture > osm > user_suggested."""
    assert trust_priority("admin") > trust_priority("sirene")
    assert trust_priority("sirene") > trust_priority("overture")
    assert trust_priority("overture") > trust_priority("osm")
    assert trust_priority("osm") > trust_priority("user_suggested")


def test_trust_priority_returns_intenum():
    """Each known source maps to the matching TrustPriority enum member."""
    assert trust_priority("admin") is TrustPriority.ADMIN
    assert trust_priority("sirene") is TrustPriority.SIRENE
    assert trust_priority("overture") is TrustPriority.OVERTURE
    assert trust_priority("osm") is TrustPriority.OSM
    assert trust_priority("user_suggested") is TrustPriority.USER_SUGGESTED


def test_trust_priority_raises_on_unknown_source():
    with pytest.raises(ValueError, match="unknown source"):
        trust_priority("foobar")


# ============================================================
# find_match — by SIRET (exact)
# ============================================================


def test_find_match_by_siret_exact(db: Session):
    """Candidate.siret matches existing store.siret → returns that store."""
    retailer_id = _seed_retailer(db)
    store_id = _seed_store(db, retailer_id=retailer_id, siret="12345678900015")
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret="12345678900015",
        osm_id=None,
        retailer_id=retailer_id,
    )
    match = find_match(db, candidate)
    assert match is not None
    assert match.id == store_id


def test_find_match_by_siret_returns_none_when_no_match(db: Session):
    retailer_id = _seed_retailer(db)
    _seed_store(db, retailer_id=retailer_id, siret="11111111100015")
    candidate = CandidateStore(
        source="sirene",
        name="X",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret="99999999900015",
        osm_id=None,
        retailer_id=None,
    )
    assert find_match(db, candidate) is None


# ============================================================
# find_match — by osm_id (exact)
# ============================================================


def test_find_match_by_osm_id_exact(db: Session):
    """Candidate.osm_id matches existing store.osm_id → returns that store."""
    retailer_id = _seed_retailer(db)
    store_id = _seed_store(db, retailer_id=retailer_id, osm_id=42424242)
    candidate = CandidateStore(
        source="osm",
        name="Carrefour Market Bastille",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret=None,
        osm_id=42424242,
        retailer_id=retailer_id,
    )
    match = find_match(db, candidate)
    assert match is not None
    assert match.id == store_id


def test_find_match_siret_wins_over_osm_id(db: Session):
    """SIRET is checked first — if it matches, we return that row even if a
    different osm_id would also match a different store."""
    retailer_id = _seed_retailer(db)
    store_a = _seed_store(db, retailer_id=retailer_id, siret="12345678900015", osm_id=11111)
    store_b = _seed_store(  # noqa: F841 — present in DB but should NOT be returned
        db,
        retailer_id=retailer_id,
        siret="22222222200015",
        osm_id=22222,
        address="autre adresse",
    )
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret="12345678900015",  # matches store_a
        osm_id=22222,  # would match store_b
        retailer_id=retailer_id,
    )
    match = find_match(db, candidate)
    assert match is not None
    assert match.id == store_a, "SIRET match must take precedence over osm_id"


# ============================================================
# find_match — fuzzy (lat/lng + retailer + name)
# ============================================================


def test_find_match_fuzzy_within_radius_same_retailer(db: Session):
    """Candidate ~30 m away, same retailer, similar name → fuzzy match."""
    retailer_id = _seed_retailer(db)
    # Paris Bastille at (48.853, 2.369) — store on file.
    store_id = _seed_store(
        db,
        retailer_id=retailer_id,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        name="Carrefour Market Bastille",
        siret=None,
        osm_id=None,
    )
    # Candidate ~30 m north-east of the seeded store.
    # 0.0002° lat ~ 22 m ; 0.0002° lng ~ 14.7 m at 48° → ~26 m total.
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",  # identical → trgm = 1.0
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853200"),
        lng=Decimal("2.369200"),
        siret="12345678900015",  # candidate has SIRET but seeded store doesn't
        osm_id=None,
        retailer_id=retailer_id,
    )
    match = find_match(db, candidate)
    assert match is not None
    assert match.id == store_id


def test_find_match_fuzzy_no_match_when_too_far(db: Session):
    """Candidate >100 m away → no fuzzy match."""
    retailer_id = _seed_retailer(db)
    _seed_store(
        db,
        retailer_id=retailer_id,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret=None,
        osm_id=None,
    )
    # ~1 km north — well above the 50 m default radius.
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.862000"),
        lng=Decimal("2.369000"),
        siret=None,
        osm_id=None,
        retailer_id=retailer_id,
    )
    assert find_match(db, candidate) is None


def test_find_match_fuzzy_no_match_when_diff_retailer(db: Session):
    """Same physical location, different retailer_id → no fuzzy match.

    Defends against merging two distinct enseignes that happen to share a
    rooftop (mall food court, dual-branded outlets).
    """
    retailer_a = _seed_retailer(db, name="Carrefour", slug="carrefour")
    retailer_b = _seed_retailer(db, name="Monoprix", slug="monoprix")
    _seed_store(
        db,
        retailer_id=retailer_a,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        name="Carrefour Market Bastille",
        siret=None,
        osm_id=None,
    )
    candidate = CandidateStore(
        source="sirene",
        name="Monoprix Bastille",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853000"),  # same coords
        lng=Decimal("2.369000"),
        siret="22222222200015",
        osm_id=None,
        retailer_id=retailer_b,  # different retailer
    )
    assert find_match(db, candidate) is None


def test_find_match_fuzzy_no_match_when_name_too_dissimilar(db: Session):
    """Same coords, same retailer, but name trgm score below threshold → None.

    Defends against merging a "Carrefour Market" with a "Carrefour Station-
    Service" that share a parking lot but are distinct PoS.
    """
    retailer_id = _seed_retailer(db)
    _seed_store(
        db,
        retailer_id=retailer_id,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        name="Carrefour Market Bastille",
        siret=None,
        osm_id=None,
    )
    candidate = CandidateStore(
        source="sirene",
        # Drastically different — short, no shared trigrams beyond "car".
        name="Zyx Qwe",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853100"),
        lng=Decimal("2.369100"),
        siret="22222222200015",
        osm_id=None,
        retailer_id=retailer_id,
    )
    assert find_match(db, candidate) is None


# ============================================================
# apply_upsert — INSERT path
# ============================================================


def test_apply_upsert_insert_when_no_match(db: Session):
    retailer_id = _seed_retailer(db)
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Nation",
        address="5 Place de la Nation",
        city="Paris",
        postal_code="75011",
        lat=Decimal("48.848000"),
        lng=Decimal("2.396000"),
        siret="33333333300015",
        osm_id=None,
        retailer_id=retailer_id,
    )
    result = apply_upsert(db, candidate)
    assert isinstance(result, UpsertResult)
    assert result.action == "inserted"
    assert result.store_id is not None
    # Round-trip — the row exists and carries the candidate's fields.
    row = db.execute(
        text("SELECT siret, source, name, lat FROM stores WHERE id = :id"),
        {"id": result.store_id},
    ).first()
    assert row.siret == "33333333300015"
    assert row.source == "sirene"
    assert row.name == "Carrefour Market Nation"
    assert row.lat == Decimal("48.848000")


# ============================================================
# apply_upsert — UPDATE-in-place (same source)
# ============================================================


def test_apply_upsert_update_in_place_same_source(db: Session):
    """Existing SIRENE row + candidate SIRENE with same SIRET → in-place
    UPDATE of mutable fields. No source change."""
    retailer_id = _seed_retailer(db)
    store_id = _seed_store(
        db,
        retailer_id=retailer_id,
        source="sirene",
        siret="44444444400015",
        name="Carrefour Market Old Name",
        address="Old address",
    )
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market New Name",
        address="New address",
        city="Paris",
        postal_code="75011",
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret="44444444400015",
        osm_id=None,
        retailer_id=retailer_id,
    )
    result = apply_upsert(db, candidate)
    assert result.action == "updated"
    assert result.store_id == store_id
    row = db.execute(
        text("SELECT name, address, source FROM stores WHERE id = :id"),
        {"id": store_id},
    ).first()
    assert row.name == "Carrefour Market New Name"
    assert row.address == "New address"
    assert row.source == "sirene"  # unchanged


# ============================================================
# apply_upsert — MERGE (lower-trust → higher-trust)
# ============================================================


def test_apply_upsert_merge_sirene_over_osm_via_fuzzy(db: Session):
    """Existing OSM row (no SIRET, has osm_id), candidate SIRENE fuzzy match
    → upgrade source to 'sirene', preserve osm_id, populate siret."""
    retailer_id = _seed_retailer(db)
    store_id = _seed_store(
        db,
        retailer_id=retailer_id,
        source="osm",
        osm_id=55555,
        siret=None,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        name="Carrefour Market Bastille",
    )
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",
        address="Updated",
        city="Paris",
        postal_code="75011",
        lat=Decimal("48.853100"),  # 11 m away → fuzzy match
        lng=Decimal("2.369100"),
        siret="55555555500015",
        osm_id=None,
        retailer_id=retailer_id,
    )
    result = apply_upsert(db, candidate)
    assert result.action == "merged"
    assert result.store_id == store_id
    row = db.execute(
        text("SELECT source, osm_id, siret FROM stores WHERE id = :id"),
        {"id": store_id},
    ).first()
    assert row.source == "sirene"  # upgraded
    assert row.osm_id == 55555  # preserved
    assert row.siret == "55555555500015"  # populated


# ============================================================
# apply_upsert — PRESERVE (higher-trust shields lower-trust)
# ============================================================


def test_apply_upsert_preserve_admin_over_sirene(db: Session):
    """Existing admin row, candidate SIRENE fuzzy match → source stays 'admin',
    only NULL fields (siret) get backfilled.

    The admin row carries a hand-tuned name ("... — réouverture mars 2026")
    that we DON'T want SIRENE to overwrite. Names are similar enough to
    fuzzy-match, but the candidate's name is not the canonical display value.
    """
    retailer_id = _seed_retailer(db)
    store_id = _seed_store(
        db,
        retailer_id=retailer_id,
        source="admin",
        siret=None,
        osm_id=None,
        name="Carrefour Market Bastille — réouverture mars 2026",
        address="Admin-curated address",
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
    )
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",  # would overwrite if not preserved
        address="SIRENE address",
        city="Paris",
        postal_code="75011",
        lat=Decimal("48.853100"),
        lng=Decimal("2.369100"),
        siret="66666666600015",
        osm_id=None,
        retailer_id=retailer_id,
    )
    result = apply_upsert(db, candidate)
    assert result.action == "preserved"
    assert result.store_id == store_id
    row = db.execute(
        text("SELECT source, siret, name, address FROM stores WHERE id = :id"),
        {"id": store_id},
    ).first()
    assert row.source == "admin"  # NOT changed
    assert row.siret == "66666666600015"  # NULL → backfilled
    # admin-curated fields preserved
    assert row.name == "Carrefour Market Bastille — réouverture mars 2026"
    assert row.address == "Admin-curated address"


# ============================================================
# apply_upsert — CONFLICT (same source, fuzzy match, different ident)
# ============================================================


def test_apply_upsert_conflict_two_sirene_diff_siret(db: Session):
    """Existing SIRENE store with SIRET=A, candidate SIRENE SIRET=B fuzzy match
    → conflict logged, no write."""
    retailer_id = _seed_retailer(db)
    store_id_a = _seed_store(
        db,
        retailer_id=retailer_id,
        source="sirene",
        siret="77777777700015",
        name="Carrefour Market Bastille",
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
    )
    captured: list[str] = []
    candidate = CandidateStore(
        source="sirene",
        name="Carrefour Market Bastille",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853100"),
        lng=Decimal("2.369100"),
        siret="88888888800015",  # DIFFERENT siret, same fuzzy footprint
        osm_id=None,
        retailer_id=retailer_id,
    )
    result = apply_upsert(db, candidate, conflict_log=captured.append)
    assert result.action == "conflict"
    assert result.store_id == store_id_a  # surfaces the colliding row
    assert captured, "conflict_log callback should have been called"
    # Confirm nothing new was inserted, and store_a's siret unchanged.
    row = db.execute(
        text("SELECT siret FROM stores WHERE id = :id"),
        {"id": store_id_a},
    ).first()
    assert row.siret == "77777777700015"
    count = db.execute(text("SELECT COUNT(*) FROM stores")).scalar()
    assert count == 1, "no new row should have been inserted on conflict"


# ============================================================
# apply_upsert — caller is responsible for commit (R-DB-02)
# ============================================================


def test_apply_upsert_does_not_commit(db: Session):
    """The helper flushes but never commits — caller decides when to commit.

    Validated indirectly : at the end of the test, the SAVEPOINT teardown
    rolls back, and ``stores`` ends empty (no autocommit slipped through).
    """
    retailer_id = _seed_retailer(db)
    candidate = CandidateStore(
        source="osm",
        name="Carrefour Market Test",
        address=None,
        city=None,
        postal_code=None,
        lat=Decimal("48.853000"),
        lng=Decimal("2.369000"),
        siret=None,
        osm_id=99999,
        retailer_id=retailer_id,
    )
    result = apply_upsert(db, candidate)
    assert result.action == "inserted"
    # Sanity within the SAVEPOINT — the row IS visible to subsequent reads
    # in the same session even though we never called db.commit().
    assert db.execute(text("SELECT COUNT(*) FROM stores WHERE id = :id"), {"id": result.store_id}).scalar() == 1
