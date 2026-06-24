"""Tests for the osm_bulk_import addr-strict / silent-skip fix.

Context (2026-04-30) — bulk import OSM unique du 27/04/2026 a manqué un
Intermarché Express Courbevoie (way ``1293099937``, tagué shop=convenience +
name + addr:housenumber/street MAIS sans addr:city/postcode et représenté
comme une *way* (polygone du bâtiment), pas un node).

Deux causes racines confirmées :

1. Le handler PBF skippait silencieusement TOUTES les ways
   (``skipped_invalid += 1`` sans logging ni résolution de géométrie).
2. Tous les autres skips (null-island, missing-name, missing-coords) étaient
   incrémentés sans logging structured — impossible de quantifier l'impact en
   prod ou de corréler un store manquant avec un osm_id précis.

Ce module ajoute la couverture régression :

- Insertion best-effort des stores avec adresse partielle (sans city/postcode)
- Insertion des ways via résolution de la géométrie (centroïde des nodes)
- Logging structured INFO sur tous les skip events avec ``osm_id`` + raison
- Skip discipline : seuls ``name`` + ``lat/lng`` sont obligatoires
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from tests.test_osm_bulk_import import _write_synthetic_pbf  # reuse helper


@pytest.fixture
def cfg():
    return {
        "shop_types": ["supermarket", "convenience", "bakery", "butcher", "greengrocer"],
        "country_code": "FR",
        "overpass_timeout": 30,
        "batch_chunk_size": 1000,
    }


@pytest.fixture
def pbf_path(tmp_path):
    return tmp_path / "synthetic_addr.osm.pbf"


# ---------------------------------------------------------------------------
# Best-effort insertion : address tags optional
# ---------------------------------------------------------------------------


class TestBestEffortAddressInsertion:
    """Stores with partial address must still import. Only name + coords are required."""

    def test_imports_store_without_addr_city(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 80001,
                    "lat": 48.86,
                    "lon": 2.35,
                    "tags": {
                        "shop": "supermarket",
                        "name": "No City Shop",
                        "addr:housenumber": "10",
                        "addr:street": "rue des Tests",
                        "addr:postcode": "75001",
                        # No addr:city
                    },
                }
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 1
        with session_factory() as db:
            row = db.execute(text("SELECT name, city, postal_code FROM stores WHERE osm_id = 80001")).first()
            assert row is not None
            assert row.name == "No City Shop"
            assert row.city is None
            assert row.postal_code == "75001"

    def test_imports_store_without_addr_postcode(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 80002,
                    "lat": 48.86,
                    "lon": 2.36,
                    "tags": {
                        "shop": "convenience",
                        "name": "No Postcode Shop",
                        "addr:housenumber": "20",
                        "addr:street": "rue Sans Code",
                        "addr:city": "Paris",
                        # No addr:postcode
                    },
                }
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 1
        with session_factory() as db:
            row = db.execute(text("SELECT city, postal_code FROM stores WHERE osm_id = 80002")).first()
            assert row is not None
            assert row.city == "Paris"
            assert row.postal_code is None

    def test_imports_store_with_only_name_and_coords(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 80003,
                    "lat": 48.87,
                    "lon": 2.34,
                    "tags": {"shop": "bakery", "name": "Bare Minimum"},
                }
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 1
        with session_factory() as db:
            row = db.execute(text("SELECT name, address, city, postal_code FROM stores WHERE osm_id = 80003")).first()
            assert row is not None
            assert row.name == "Bare Minimum"
            assert row.address is None
            assert row.city is None
            assert row.postal_code is None


# ---------------------------------------------------------------------------
# Way support (Intermarché Courbevoie regression)
# ---------------------------------------------------------------------------


class TestWayGeometrySupport:
    """Ways tagged shop=* must be imported using their geometry centroid."""

    def test_imports_shop_tagged_way_with_geometry_centroid(self, pbf_path, cfg, session_factory):
        """Regression : way 1293099937 (Intermarché Courbevoie) was skipped silently.

        A way tagged shop=convenience with a polygonal geometry must import
        with a center derived from its constituent nodes.
        """
        from osm_bulk_import import run_bulk_import

        # Build a way from 4 corner nodes (rough rectangle around Courbevoie).
        # Center should land near (48.8965, 2.2575).
        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {"id": 90001, "lat": 48.8964, "lon": 2.2574, "tags": {}},
                {"id": 90002, "lat": 48.8964, "lon": 2.2576, "tags": {}},
                {"id": 90003, "lat": 48.8966, "lon": 2.2576, "tags": {}},
                {"id": 90004, "lat": 48.8966, "lon": 2.2574, "tags": {}},
            ],
            ways=[
                {
                    "id": 91293099937,
                    "nodes": [90001, 90002, 90003, 90004, 90001],
                    "tags": {
                        "shop": "convenience",
                        "name": "Intermarché Express Courbevoie",
                        "brand": "Intermarché Express",
                        "addr:housenumber": "18 ter",
                        "addr:street": "rue de Bezons",
                    },
                }
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 1
        with session_factory() as db:
            row = db.execute(text("SELECT name, address, lat, lng FROM stores WHERE osm_id = 91293099937")).first()
            assert row is not None
            assert row.name == "Intermarché Express Courbevoie"
            assert row.address == "18 ter rue de Bezons"
            # Centroid in the rectangle bounds
            assert 48.8964 <= float(row.lat) <= 48.8966
            assert 2.2574 <= float(row.lng) <= 2.2576

    def test_skips_way_without_resolvable_geometry(self, pbf_path, cfg, session_factory, caplog):
        """Way with node refs that aren't in the PBF → cannot resolve, skip with log."""
        from osm_bulk_import import run_bulk_import

        # Way references nodes that don't exist in the PBF
        _write_synthetic_pbf(
            pbf_path,
            nodes=[],
            ways=[
                {
                    "id": 92000001,
                    "nodes": [99991, 99992, 99993],
                    "tags": {"shop": "supermarket", "name": "Phantom Way"},
                }
            ],
        )
        with caplog.at_level(logging.INFO, logger="osm_bulk_import"):
            stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 0
        # Skip event must be logged with the osm_id and a reason
        skip_records = [r for r in caplog.records if "92000001" in r.message and "skip" in r.message.lower()]
        assert len(skip_records) >= 1, (
            f"Expected a structured skip log mentioning osm_id 92000001; got: {[r.message for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Skip discipline : structured logging on every skip event
# ---------------------------------------------------------------------------


class TestStructuredSkipLogging:
    """Every skip event must emit a structured INFO log with osm_id + reason.

    Anti-pattern : silent ``pass`` / ``continue`` / ``+= 1`` without trace
    makes prod ops opaque (impossible to know WHICH stores were dropped).
    """

    def test_logs_skip_when_name_missing(self, pbf_path, cfg, session_factory, caplog):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 81001,
                    "lat": 48.86,
                    "lon": 2.35,
                    # No name tag
                    "tags": {"shop": "supermarket", "addr:city": "Paris"},
                }
            ],
        )
        with caplog.at_level(logging.INFO, logger="osm_bulk_import"):
            stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 0
        assert stats["skipped_invalid"] == 1
        skip_records = [r for r in caplog.records if "81001" in r.message and "name" in r.message.lower()]
        assert len(skip_records) >= 1, (
            f"Expected structured skip log for osm_id=81001 mentioning 'name'; "
            f"got: {[r.message for r in caplog.records]}"
        )

    def test_logs_skip_for_null_island(self, pbf_path, cfg, session_factory, caplog):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 81002,
                    "lat": 0.0,
                    "lon": 0.0,
                    "tags": {"shop": "supermarket", "name": "Null Island Shop"},
                }
            ],
        )
        with caplog.at_level(logging.INFO, logger="osm_bulk_import"):
            stats = run_bulk_import(session_factory, cfg, pbf_path)
        assert stats["inserted"] == 0
        skip_records = [
            r
            for r in caplog.records
            if "81002" in r.message and ("null" in r.message.lower() or "coord" in r.message.lower())
        ]
        assert len(skip_records) >= 1, (
            f"Expected structured skip log for null-island osm_id=81002; got: {[r.message for r in caplog.records]}"
        )
