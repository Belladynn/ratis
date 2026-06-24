"""Pattern C — verify ratis_batch_osm_sync calls init_sentry in main()."""

from __future__ import annotations

import osm_bulk_import
import osm_sync


def test_osm_sync_main_calls_init_sentry_with_batch_name(monkeypatch):
    """osm_sync.main() must call init_sentry first."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setenv("OSM_OVERPASS_URL", "https://overpass.example/api")
    monkeypatch.setattr("sys.argv", ["osm_sync.py", "--dry-run"])

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(osm_sync, "init_sentry", _capture_then_raise)

    try:
        osm_sync.main()
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_osm_sync"]


def test_osm_bulk_import_main_calls_init_sentry_with_batch_name(monkeypatch):
    """osm_bulk_import.main() must call init_sentry first."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["osm_bulk_import.py", "--dry-run"])

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(osm_bulk_import, "init_sentry", _capture_then_raise)

    try:
        osm_bulk_import.main()
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_osm_sync"]
