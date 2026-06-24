"""Tests for the ``sirene_geocode_cache`` model (SIRENE PR1).

Two layers :
- Pure model contract (no DB) — column types, PK, server defaults, indexes.
- DB roundtrip (consumes the ``db`` fixture) — INSERT, UPDATE, address_hash
  index lookup, PK uniqueness on ``siret``.

The cache table memoises Géoplateforme bulk-geocoding answers keyed by SIRET.
``address_hash`` lets us detect when the SIRENE record's address changed and
re-geocode only those rows.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from ratis_core.models.sirene_geocode_cache import SireneGeocodeCache
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

# ── helpers ──────────────────────────────────────────────────────────────────


def _col(model, name: str) -> sa.Column:
    mapper = sa_inspect(model)
    return mapper.columns[name]


# ── Model contract (no DB) ────────────────────────────────────────────────────


class TestSireneGeocodeCacheModelContract:
    def test_table_name(self):
        assert SireneGeocodeCache.__tablename__ == "sirene_geocode_cache"

    def test_siret_is_primary_key_char14(self):
        col = _col(SireneGeocodeCache, "siret")
        assert col.primary_key is True
        assert isinstance(col.type, sa.CHAR)
        assert col.type.length == 14

    def test_address_hash_text_not_null(self):
        col = _col(SireneGeocodeCache, "address_hash")
        assert col.nullable is False
        assert isinstance(col.type, sa.Text)

    def test_lat_lng_numeric_9_6_nullable(self):
        for name in ("lat", "lng"):
            col = _col(SireneGeocodeCache, name)
            assert col.nullable is True, f"{name} should be nullable (geocoding can fail)"
            assert isinstance(col.type, sa.Numeric)
            assert col.type.precision == 9
            assert col.type.scale == 6

    def test_score_numeric_3_2_nullable(self):
        col = _col(SireneGeocodeCache, "score")
        assert col.nullable is True
        assert isinstance(col.type, sa.Numeric)
        assert col.type.precision == 3
        assert col.type.scale == 2

    def test_geocoded_at_has_server_default(self):
        col = _col(SireneGeocodeCache, "geocoded_at")
        assert col.nullable is False
        assert col.server_default is not None

    def test_address_hash_index_declared(self):
        index_names = {idx.name for idx in SireneGeocodeCache.__table__.indexes}
        assert "ix_sirene_geocode_cache_address_hash" in index_names, (
            f"Expected ix_sirene_geocode_cache_address_hash, found: {index_names}"
        )


# ── DB roundtrip ─────────────────────────────────────────────────────────────


class TestSireneGeocodeCacheRoundtrip:
    def test_insert_and_fetch(self, db):
        row = SireneGeocodeCache(
            siret="12345678900012",
            address_hash="hash-abc",
            lat=Decimal("48.856600"),
            lng=Decimal("2.352200"),
            score=Decimal("0.95"),
        )
        db.add(row)
        db.flush()

        fetched = db.query(SireneGeocodeCache).filter_by(siret="12345678900012").one()
        assert fetched.address_hash == "hash-abc"
        assert fetched.lat == Decimal("48.856600")
        assert fetched.lng == Decimal("2.352200")
        assert fetched.score == Decimal("0.95")
        assert fetched.geocoded_at is not None

    def test_insert_with_null_lat_lng_score(self, db):
        """Geocoding may fail — we still cache the attempt with NULLs."""
        row = SireneGeocodeCache(
            siret="98765432100015",
            address_hash="hash-fail",
        )
        db.add(row)
        db.flush()

        fetched = db.query(SireneGeocodeCache).filter_by(siret="98765432100015").one()
        assert fetched.lat is None
        assert fetched.lng is None
        assert fetched.score is None

    def test_siret_primary_key_unique(self, db):
        db.add(
            SireneGeocodeCache(
                siret="11111111100011",
                address_hash="h1",
            )
        )
        db.flush()

        db.add(
            SireneGeocodeCache(
                siret="11111111100011",
                address_hash="h2",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()

    def test_address_hash_lookup(self, db):
        """Lookups by address_hash power the 'has the address changed?' check."""
        db.add(
            SireneGeocodeCache(
                siret="22222222200022",
                address_hash="shared-hash",
                lat=Decimal("50.000000"),
                lng=Decimal("3.000000"),
            )
        )
        db.add(
            SireneGeocodeCache(
                siret="33333333300033",
                address_hash="shared-hash",
                lat=Decimal("50.000001"),
                lng=Decimal("3.000001"),
            )
        )
        db.flush()

        rows = (
            db.query(SireneGeocodeCache).filter_by(address_hash="shared-hash").order_by(SireneGeocodeCache.siret).all()
        )
        assert [r.siret for r in rows] == ["22222222200022", "33333333300033"]
