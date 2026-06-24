"""Tests for ratis_batch_osm_sync.osm_sync."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text


class TestNormalizeOsmElement:
    """Unit tests — no DB needed."""

    def test_node_basic(self):
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 123,
            "lat": 48.856,
            "lon": 2.352,
            "tags": {
                "name": "MONOPRIX COURBEVOIE",
                "brand": "Monoprix",
                "addr:city": "Courbevoie",
                "addr:postcode": "92400",
                "phone": "+33 1 49 97 09 70",
                "ref:FR:SIRET": "55208329700374",
                "opening_hours": "Mo-Sa 08:00-21:00",
            },
        }
        result = _normalize_osm_element(element, "FR")
        assert result is not None
        assert result["osm_id"] == 123
        assert result["name"] == "MONOPRIX COURBEVOIE"
        assert result["phone"] == "0149970970"
        assert result["siret"] == "55208329700374"
        assert result["postal_code"] == "92400"
        assert result["lat"] == Decimal("48.856")
        assert result["lng"] == Decimal("2.352")

    def test_way_uses_center(self):
        from osm_sync import _normalize_osm_element

        element = {
            "type": "way",
            "id": 456,
            "center": {"lat": 48.85, "lon": 2.35},
            "tags": {"name": "Carrefour Market"},
        }
        result = _normalize_osm_element(element, "FR")
        assert result is not None
        assert result["lat"] == Decimal("48.85")
        assert result["lng"] == Decimal("2.35")

    def test_missing_name_returns_none(self):
        from osm_sync import _normalize_osm_element

        element = {"type": "node", "id": 789, "lat": 48.0, "lon": 2.0, "tags": {}}
        assert _normalize_osm_element(element, "FR") is None

    def test_invalid_phone_stored_as_none(self):
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 101,
            "lat": 48.0,
            "lon": 2.0,
            "tags": {"name": "Boulangerie", "phone": "INVALID"},
        }
        result = _normalize_osm_element(element, "FR")
        assert result is not None
        assert result["phone"] is None

    def test_address_assembled_from_housenumber_and_street(self):
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 202,
            "lat": 48.0,
            "lon": 2.0,
            "tags": {
                "name": "Lidl",
                "addr:housenumber": "12",
                "addr:street": "Rue de la Paix",
            },
        }
        result = _normalize_osm_element(element, "FR")
        assert result["address"] == "12 Rue de la Paix"

    def test_node_missing_lat_returns_none(self):
        from osm_sync import _normalize_osm_element

        element = {"type": "node", "id": 303, "tags": {"name": "Bakery"}}  # no lat/lon
        assert _normalize_osm_element(element, "FR") is None

    def test_way_missing_center_returns_none(self):
        from osm_sync import _normalize_osm_element

        element = {"type": "way", "id": 404, "tags": {"name": "Supermarché"}}  # no center
        assert _normalize_osm_element(element, "FR") is None

    def test_siret_with_spaces_normalized(self):
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 505,
            "lat": 48.0,
            "lon": 2.0,
            "tags": {"name": "Carrefour", "ref:FR:SIRET": "553 018 434 00034"},
        }
        result = _normalize_osm_element(element, "FR")
        assert result is not None
        assert result["siret"] == "55301843400034"

    def test_siret_invalid_length_stored_as_none(self):
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 506,
            "lat": 48.0,
            "lon": 2.0,
            "tags": {"name": "Boulangerie", "ref:FR:SIRET": "123"},
        }
        result = _normalize_osm_element(element, "FR")
        assert result is not None
        assert result["siret"] is None

    @pytest.mark.parametrize("bad_lat", [-90.001, 90.001, 200.0, -1000.0])
    def test_out_of_range_latitude_rejected(self, bad_lat):
        """Element with a latitude outside [-90, 90] must be dropped."""
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 600,
            "lat": bad_lat,
            "lon": 2.0,
            "tags": {"name": "Poison Store"},
        }
        assert _normalize_osm_element(element, "FR") is None

    @pytest.mark.parametrize("bad_lng", [-180.001, 180.001, 500.0, -9999.0])
    def test_out_of_range_longitude_rejected(self, bad_lng):
        """Element with a longitude outside [-180, 180] must be dropped."""
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 601,
            "lat": 48.0,
            "lon": bad_lng,
            "tags": {"name": "Poison Store"},
        }
        assert _normalize_osm_element(element, "FR") is None

    @pytest.mark.parametrize("lat,lng", [(-90.0, -180.0), (90.0, 180.0), (0.0, 0.0)])
    def test_boundary_coordinates_accepted(self, lat, lng):
        """Coordinates exactly on the [-90,90]/[-180,180] bounds stay valid."""
        from osm_sync import _normalize_osm_element

        element = {
            "type": "node",
            "id": 602,
            "lat": lat,
            "lon": lng,
            "tags": {"name": "Edge Store"},
        }
        result = _normalize_osm_element(element, "FR")
        assert result is not None
        assert result["lat"] == Decimal(str(lat))
        assert result["lng"] == Decimal(str(lng))


class TestUpsertStore:
    """Integration tests — require DB (from conftest fixtures)."""

    def test_store_inserted(self, db):
        from osm_sync import _upsert_store

        data = {
            "osm_id": 99001,
            "name": "Lidl Test",
            "retailer": "Lidl",
            "address": "1 rue du Test",
            "city": "Paris",
            "postal_code": "75001",
            "lat": "48.8566",
            "lng": "2.3522",
            "phone": "0145678901",
            "siret": None,
            "opening_hours": None,
        }
        _upsert_store(db, data)
        db.flush()
        row = db.execute(
            text("SELECT name, phone FROM stores WHERE osm_id = :oid"),
            {"oid": 99001},
        ).first()
        assert row is not None
        assert row.name == "Lidl Test"
        assert row.phone == "0145678901"

    def test_store_upserted_on_conflict(self, db):
        from osm_sync import _upsert_store

        base = {
            "osm_id": 99002,
            "name": "Monop",
            "retailer": None,
            "address": None,
            "city": "Lyon",
            "postal_code": "69001",
            "lat": "45.748",
            "lng": "4.847",
            "phone": None,
            "siret": None,
            "opening_hours": None,
        }
        _upsert_store(db, base)
        db.flush()
        # Second upsert with updated name
        base["name"] = "Monoprix"
        _upsert_store(db, base)
        db.flush()
        rows = db.execute(
            text("SELECT COUNT(*) AS cnt FROM stores WHERE osm_id = :oid"),
            {"oid": 99002},
        ).first()
        assert rows.cnt == 1
        row = db.execute(
            text("SELECT name FROM stores WHERE osm_id = :oid"),
            {"oid": 99002},
        ).first()
        assert row.name == "Monoprix"


class TestUpsertCity:
    def test_city_inserted(self, db):
        from osm_sync import _upsert_city

        _upsert_city(db, "92400", "Courbevoie", "FR")
        db.flush()
        row = db.execute(
            text("SELECT city_name, department FROM cities WHERE postal_code = '92400'"),
        ).first()
        assert row is not None
        assert row.city_name == "COURBEVOIE"
        assert row.department == "92"

    def test_city_upsert_idempotent(self, db):
        from osm_sync import _upsert_city

        _upsert_city(db, "75001", "Paris", "FR")
        _upsert_city(db, "75001", "Paris", "FR")  # second call must not fail
        db.flush()
        rows = db.execute(
            text("SELECT COUNT(*) AS cnt FROM cities WHERE postal_code = '75001'"),
        ).first()
        assert rows.cnt == 1


class TestRunBatch:
    def test_dry_run_does_not_write(self, session_factory):
        from osm_sync import run_batch

        mock_elements = [
            {
                "type": "node",
                "id": 55001,
                "lat": 48.86,
                "lon": 2.35,
                "tags": {
                    "name": "Franprix",
                    "addr:city": "Paris",
                    "addr:postcode": "75004",
                },
            }
        ]
        cfg = {
            "shop_types": ["supermarket"],
            "country_code": "FR",
            "overpass_timeout": 30,
            "batch_chunk_size": 500,
        }
        with patch("osm_sync.fetch_osm_elements", return_value=mock_elements):
            stats = run_batch(session_factory, cfg, "http://fake", dry_run=True)
        assert stats["inserted"] == 1
        assert stats["skipped"] == 0
        # Verify nothing was written to DB
        with session_factory() as db:
            row = db.execute(
                text("SELECT COUNT(*) AS cnt FROM stores WHERE osm_id = 55001"),
            ).first()
            assert row.cnt == 0

    def test_run_batch_upserts_stores_and_cities(self, session_factory):
        from osm_sync import run_batch

        mock_elements = [
            {
                "type": "node",
                "id": 55002,
                "lat": 48.86,
                "lon": 2.35,
                "tags": {
                    "name": "Intermarché",
                    "addr:city": "Bordeaux",
                    "addr:postcode": "33000",
                    "phone": "0556789012",
                },
            }
        ]
        cfg = {
            "shop_types": ["supermarket"],
            "country_code": "FR",
            "overpass_timeout": 30,
            "batch_chunk_size": 500,
        }
        with patch("osm_sync.fetch_osm_elements", return_value=mock_elements):
            stats = run_batch(session_factory, cfg, "http://fake", dry_run=False)

        assert stats["inserted"] == 1
        assert stats["cities_upserted"] == 1
        with session_factory() as db:
            row = db.execute(
                text("SELECT name, phone FROM stores WHERE osm_id = 55002"),
            ).first()
            assert row.name == "Intermarché"
            assert row.phone == "0556789012"
            city = db.execute(
                text("SELECT city_name FROM cities WHERE postal_code = '33000'"),
            ).first()
            assert city.city_name == "BORDEAUX"


class TestMain:
    """Regression tests for osm_sync.main() env-var wiring.

    ``require_env()`` in ratis_core.startup validates env vars but returns None.
    Assigning its return to a variable (instead of reading os.environ) was a
    latent bug — make_engine(None) would crash silently at runtime.
    """

    def test_main_passes_real_database_url_to_make_engine(self, monkeypatch):
        import osm_sync

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
        monkeypatch.setenv("OSM_OVERPASS_URL", "https://overpass.example/api")

        fake_engine = MagicMock()
        monkeypatch.setattr(osm_sync, "make_engine", MagicMock(return_value=fake_engine))
        monkeypatch.setattr(osm_sync, "sessionmaker", MagicMock())
        monkeypatch.setattr(osm_sync, "run_batch", MagicMock())
        # load_settings returns the osm_sync config section
        monkeypatch.setattr(
            osm_sync,
            "load_settings",
            lambda: {"osm_sync": {"shop_types": [], "country_code": "FR", "batch_chunk_size": 1}},
        )
        # CLI: no args → dry_run default False
        monkeypatch.setattr("sys.argv", ["osm_sync.py"])

        osm_sync.main()

        # Critical assertion : make_engine must receive the ACTUAL env value,
        # not None (which is what require_env returns).
        osm_sync.make_engine.assert_called_once_with("postgresql+psycopg://ratis:ratis@host/db")
        # Overpass URL also reaches run_batch correctly
        call_kwargs = osm_sync.run_batch.call_args
        assert "https://overpass.example/api" in call_kwargs.args or any(
            v == "https://overpass.example/api" for v in call_kwargs.kwargs.values()
        )

    def test_main_raises_when_database_url_missing(self, monkeypatch):
        import osm_sync

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("OSM_OVERPASS_URL", "https://overpass.example/api")
        monkeypatch.setattr("sys.argv", ["osm_sync.py"])

        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            osm_sync.main()


class TestSyncLog:
    """osm_sync must trace each run in batch_sync_log like off_sync/purge."""

    def test_write_sync_log_success(self, session_factory):
        import osm_sync

        osm_sync._write_sync_log(session_factory, "success", 42, dry_run=False)
        with session_factory() as db:
            row = db.execute(
                text(
                    "SELECT batch_name, status, rows_affected FROM batch_sync_log "
                    "WHERE batch_name = :n ORDER BY id DESC LIMIT 1"
                ),
                {"n": osm_sync.BATCH_NAME},
            ).first()
        assert row is not None
        assert row.status == "success"
        assert row.rows_affected == 42

    def test_write_sync_log_dry_run_writes_nothing(self, session_factory):
        import osm_sync

        osm_sync._write_sync_log(session_factory, "success", 5, dry_run=True)
        with session_factory() as db:
            row = db.execute(
                text("SELECT COUNT(*) AS cnt FROM batch_sync_log WHERE batch_name = :n"),
                {"n": osm_sync.BATCH_NAME},
            ).first()
        assert row.cnt == 0

    def test_run_and_log_writes_success_row(self, session_factory):
        """A successful run records status='success' with the inserted count."""
        import osm_sync

        mock_elements = [
            {
                "type": "node",
                "id": 56001,
                "lat": 48.86,
                "lon": 2.35,
                "tags": {"name": "Casino"},
            }
        ]
        cfg = {
            "shop_types": ["supermarket"],
            "country_code": "FR",
            "overpass_timeout": 30,
            "batch_chunk_size": 500,
        }
        with patch("osm_sync.fetch_osm_elements", return_value=mock_elements):
            osm_sync.run_and_log(session_factory, cfg, "http://fake", dry_run=False)
        with session_factory() as db:
            row = db.execute(
                text("SELECT status, rows_affected FROM batch_sync_log WHERE batch_name = :n ORDER BY id DESC LIMIT 1"),
                {"n": osm_sync.BATCH_NAME},
            ).first()
        assert row is not None
        assert row.status == "success"
        assert row.rows_affected == 1

    def test_run_and_log_writes_failed_row_on_crash(self, session_factory):
        """A crash mid-run must still leave a status='failed' trace + re-raise."""
        import osm_sync

        cfg = {"shop_types": [], "country_code": "FR", "batch_chunk_size": 500}
        with (
            patch("osm_sync.fetch_osm_elements", side_effect=RuntimeError("overpass down")),
            pytest.raises(RuntimeError, match="overpass down"),
        ):
            osm_sync.run_and_log(session_factory, cfg, "http://fake", dry_run=False)
        with session_factory() as db:
            row = db.execute(
                text("SELECT status FROM batch_sync_log WHERE batch_name = :n ORDER BY id DESC LIMIT 1"),
                {"n": osm_sync.BATCH_NAME},
            ).first()
        assert row is not None
        assert row.status == "failed"
