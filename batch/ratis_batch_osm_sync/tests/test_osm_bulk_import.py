"""Tests for ratis_batch_osm_sync.osm_bulk_import — PBF streaming path.

Uses a synthetic PBF generated on the fly via ``osmium.SimpleWriter`` so
fixtures stay tiny, reproducible and don't need to be committed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import osmium
import pytest
from sqlalchemy import text


def _write_synthetic_pbf(
    path: Path,
    nodes: list[dict],
    ways: list[dict] | None = None,
) -> None:
    """Write a minimal PBF with the given nodes/ways.

    Each ``node`` dict: ``{"id": int, "lat": float, "lon": float, "tags": dict}``.
    Ways dict: ``{"id": int, "nodes": [int, ...], "tags": dict}``.
    ``osmium.SimpleWriter`` expects ``location=(lon, lat)`` on mutable nodes.
    """
    if path.exists():
        path.unlink()
    writer = osmium.SimpleWriter(str(path))
    try:
        for n in nodes:
            writer.add_node(
                osmium.osm.mutable.Node(
                    id=n["id"],
                    location=(n["lon"], n["lat"]),
                    tags=n.get("tags", {}),
                )
            )
        for w in ways or []:
            writer.add_way(
                osmium.osm.mutable.Way(
                    id=w["id"],
                    nodes=w["nodes"],
                    tags=w.get("tags", {}),
                )
            )
    finally:
        writer.close()


@pytest.fixture
def pbf_path(tmp_path):
    """Yield a Path suitable for a throwaway PBF in tmp_path."""
    return tmp_path / "synthetic.osm.pbf"


@pytest.fixture
def cfg():
    return {
        "shop_types": ["supermarket", "convenience", "bakery", "butcher", "greengrocer"],
        "country_code": "FR",
        "overpass_timeout": 30,
        "batch_chunk_size": 1000,
    }


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------


class TestBulkImport:
    def test_bulk_imports_shops_from_synthetic_pbf(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        # Distinct addresses required to avoid unique_store collision
        # (composite UNIQUE on (retailer, address, postal_code) NULL-safe)
        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 70001,
                    "lat": 48.86,
                    "lon": 2.35,
                    "tags": {
                        "shop": "supermarket",
                        "name": "Shop A",
                        "addr:housenumber": "1",
                        "addr:street": "rue A",
                        "addr:postcode": "75001",
                    },
                },
                {
                    "id": 70002,
                    "lat": 45.74,
                    "lon": 4.84,
                    "tags": {
                        "shop": "bakery",
                        "name": "Shop B",
                        "addr:housenumber": "2",
                        "addr:street": "rue B",
                        "addr:postcode": "69001",
                    },
                },
                {
                    "id": 70003,
                    "lat": 43.29,
                    "lon": 5.39,
                    "tags": {
                        "shop": "butcher",
                        "name": "Shop C",
                        "addr:housenumber": "3",
                        "addr:street": "rue C",
                        "addr:postcode": "13001",
                    },
                },
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)

        assert stats["inserted"] == 3
        with session_factory() as db:
            row = db.execute(text("SELECT COUNT(*) AS cnt FROM stores WHERE osm_id IN (70001, 70002, 70003)")).first()
            assert row.cnt == 3

    def test_bulk_skips_non_shop_elements(self, pbf_path, cfg, session_factory):
        """Fast-path semantics for skipped_non_shop:

        - Nodes WITHOUT a ``shop`` tag return at C-level and are NOT counted
          (otherwise we'd materialize the tag dict for 600M+ untagged nodes).
        - Nodes WITH ``shop=<not-in-whitelist>`` increment ``skipped_non_shop``.
        """
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                # Kept: matching shop type
                {"id": 70010, "lat": 48.86, "lon": 2.35, "tags": {"shop": "supermarket", "name": "Keep"}},
                # Skipped (counted): has shop tag but type is not in whitelist
                {"id": 70011, "lat": 48.86, "lon": 2.35, "tags": {"shop": "car_repair", "name": "Skip"}},
                {"id": 70012, "lat": 48.86, "lon": 2.35, "tags": {"shop": "hairdresser", "name": "Skip"}},
                # Not counted (fast-path): no shop tag at all → like most OSM nodes
                {"id": 70013, "lat": 48.86, "lon": 2.35, "tags": {"amenity": "restaurant", "name": "Silent"}},
                {"id": 70014, "lat": 48.86, "lon": 2.35, "tags": {"amenity": "bank", "name": "Silent"}},
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)

        assert stats["inserted"] == 1
        assert stats["skipped_non_shop"] == 2
        with session_factory() as db:
            row = db.execute(
                text("SELECT COUNT(*) AS cnt FROM stores WHERE osm_id IN (70010, 70011, 70012, 70013, 70014)")
            ).first()
            assert row.cnt == 1

    def test_bulk_resolves_known_retailer_alias(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        # Seed a known retailer + alias.
        with session_factory() as db:
            r = db.execute(
                text(
                    """
                    INSERT INTO retailers (canonical_name, slug, is_verified)
                    VALUES ('Carrefour', 'carrefour', true)
                    RETURNING id
                    """
                )
            ).first()
            db.execute(
                text("INSERT INTO retailer_aliases (retailer_id, alias, source) VALUES (:rid, 'carrefour', 'manual')"),
                {"rid": r.id},
            )
            db.commit()
            known_id = r.id

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 70020,
                    "lat": 48.86,
                    "lon": 2.35,
                    "tags": {"shop": "supermarket", "name": "CRF Paris", "brand": "Carrefour"},
                }
            ],
        )
        run_bulk_import(session_factory, cfg, pbf_path)

        with session_factory() as db:
            row = db.execute(text("SELECT retailer_id FROM stores WHERE osm_id = 70020")).first()
            assert row.retailer_id == known_id

    def test_bulk_creates_unverified_retailer_for_unknown_brand(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 70030,
                    "lat": 48.86,
                    "lon": 2.35,
                    "tags": {"shop": "supermarket", "name": "Chain Store", "brand": "NewChain42"},
                }
            ],
        )
        run_bulk_import(session_factory, cfg, pbf_path)

        with session_factory() as db:
            row = db.execute(
                text(
                    """
                    SELECT r.canonical_name, r.is_verified
                    FROM stores s
                    JOIN retailers r ON r.id = s.retailer_id
                    WHERE s.osm_id = 70030
                    """
                )
            ).first()
            assert row is not None
            assert row.canonical_name == "NewChain42"
            assert row.is_verified is False

    def test_bulk_upserts_existing_store_on_osm_id_conflict(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 70040,
                    "lat": 48.86,
                    "lon": 2.35,
                    "tags": {"shop": "supermarket", "name": "Initial"},
                }
            ],
        )
        run_bulk_import(session_factory, cfg, pbf_path)

        # Re-run with an updated name
        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {
                    "id": 70040,
                    "lat": 48.87,
                    "lon": 2.36,
                    "tags": {"shop": "supermarket", "name": "Updated"},
                }
            ],
        )
        run_bulk_import(session_factory, cfg, pbf_path)

        with session_factory() as db:
            rows = db.execute(text("SELECT COUNT(*) AS cnt FROM stores WHERE osm_id = 70040")).first()
            assert rows.cnt == 1
            row = db.execute(text("SELECT name FROM stores WHERE osm_id = 70040")).first()
            assert row.name == "Updated"

    def test_bulk_chunks_commit_progressively(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        cfg = {**cfg, "batch_chunk_size": 1000}
        nodes = [
            {
                "id": 71000 + i,
                "lat": 48.0 + i * 0.0001,
                "lon": 2.0 + i * 0.0001,
                "tags": {"shop": "supermarket", "name": f"S{i}"},
            }
            for i in range(2500)
        ]
        _write_synthetic_pbf(pbf_path, nodes=nodes)
        stats = run_bulk_import(session_factory, cfg, pbf_path)

        # Expect 3 commits: 1000 + 1000 + 500
        assert stats["inserted"] == 2500
        assert stats["chunks_committed"] == 3

    def test_bulk_dry_run_no_db_writes(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {"id": 72001, "lat": 48.86, "lon": 2.35, "tags": {"shop": "supermarket", "name": "Dry"}},
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path, dry_run=True)

        assert stats["inserted"] == 1
        with session_factory() as db:
            row = db.execute(text("SELECT COUNT(*) AS cnt FROM stores WHERE osm_id = 72001")).first()
            assert row.cnt == 0

    def test_disable_missing_flags_absent_stores(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        # Seed 3 OSM-sourced stores. Distinct postal_code values required to
        # avoid the composite unique_store index (NULL-safe) collapsing them.
        with session_factory() as db:
            for oid in (73001, 73002, 73003):
                db.execute(
                    text(
                        """
                        INSERT INTO stores
                          (name, osm_id, lat, lng, postal_code, is_disabled)
                        VALUES (:n, :oid, 48.0, 2.0, :pc, false)
                        """
                    ),
                    {
                        "n": f"Store {oid}",
                        "oid": oid,
                        "pc": f"7500{oid - 73000}",
                    },
                )
            db.commit()

        # PBF only has 2 of the 3
        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {"id": 73001, "lat": 48.0, "lon": 2.0, "tags": {"shop": "supermarket", "name": "Store 73001"}},
                {"id": 73002, "lat": 48.0, "lon": 2.0, "tags": {"shop": "supermarket", "name": "Store 73002"}},
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path, disable_missing=True)

        assert stats["disabled_missing"] == 1
        with session_factory() as db:
            row = db.execute(text("SELECT is_disabled, disabled_at FROM stores WHERE osm_id = 73003")).first()
            assert row.is_disabled is True
            assert row.disabled_at is not None
            # The ones present should stay enabled
            row = db.execute(text("SELECT is_disabled FROM stores WHERE osm_id = 73001")).first()
            assert row.is_disabled is False

    def test_progress_logging_fires_every_n_shop_elements(self, pbf_path, cfg, session_factory, caplog):
        """Fast-path progress logging: each 500-shop checkpoint emits one INFO line.

        The fast-path in _ShopHandler.node() only increments the progress
        counter for shop elements (post-tag-filter). Non-shop nodes return
        at C-level with no Python-side accounting.
        """
        import logging

        from osm_bulk_import import run_bulk_import

        # 1200 shops > 2 progress checkpoints (at 500 and 1000)
        nodes = [
            {
                "id": 75000 + i,
                "lat": 48.0 + i * 0.0001,
                "lon": 2.0 + i * 0.0001,
                "tags": {"shop": "supermarket", "name": f"S{i}"},
            }
            for i in range(1200)
        ]
        # 50 non-shop nodes sprinkled in — MUST NOT tick the progress counter
        # (proves the fast-path returns before _maybe_log_progress).
        nodes.extend(
            {
                "id": 76000 + i,
                "lat": 48.5,
                "lon": 2.5,
                "tags": {"amenity": "restaurant"},
            }
            for i in range(50)
        )
        _write_synthetic_pbf(pbf_path, nodes=nodes)

        with caplog.at_level(logging.INFO, logger="osm_bulk_import"):
            run_bulk_import(session_factory, cfg, pbf_path, dry_run=True)

        progress_lines = [rec for rec in caplog.records if "progress:" in rec.message]
        # 1200 shops → checkpoints at 500, 1000 (not 1500).
        assert len(progress_lines) == 2
        assert "500 shop elements seen" in progress_lines[0].message
        assert "1000 shop elements seen" in progress_lines[1].message

    def test_bulk_handles_missing_coords_gracefully(self, pbf_path, cfg, session_factory):
        from osm_bulk_import import run_bulk_import

        # SimpleWriter requires a location, but (0, 0) and -inf are sometimes
        # present in real PBFs — a "null" sentinel represented by (0, 0) is
        # valid PBF. Easier test : a way without any node reference (no
        # geometry → dropped). We simulate by asserting the handler skips
        # shop nodes at absurd coords (0, 0) only if explicitly requested.
        # For this test: write a shop node at (0, 0) which we consider
        # invalid (no real-world shop sits exactly at null island), and
        # assert the handler skips it.
        _write_synthetic_pbf(
            pbf_path,
            nodes=[
                {"id": 74001, "lat": 0.0, "lon": 0.0, "tags": {"shop": "supermarket", "name": "Null Island"}},
                {"id": 74002, "lat": 48.86, "lon": 2.35, "tags": {"shop": "supermarket", "name": "Paris"}},
            ],
        )
        stats = run_bulk_import(session_factory, cfg, pbf_path)
        # Null island is dropped via skip_null_island=True (default)
        assert stats["inserted"] == 1


# ---------------------------------------------------------------------------
# CLI / glue
# ---------------------------------------------------------------------------


class TestPyosmiumUpToDate:
    def test_update_pbf_invokes_subprocess_when_tool_present(self, pbf_path, monkeypatch):
        from osm_bulk_import import update_pbf

        # Create a dummy file so the update runs against it.
        pbf_path.write_bytes(b"\x00" * 10)

        calls = []

        def fake_which(name):
            assert name in ("pyosmium-up-to-date", "pyosmium-up-to-date.exe")
            return "/fake/bin/pyosmium-up-to-date"

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        monkeypatch.setattr("osm_bulk_import.shutil.which", fake_which)
        monkeypatch.setattr("osm_bulk_import.subprocess.run", fake_run)

        update_pbf(pbf_path)
        assert len(calls) == 1
        assert str(pbf_path) in calls[0]

    def test_update_pbf_warns_and_skips_when_tool_missing(self, pbf_path, monkeypatch, caplog):
        from osm_bulk_import import update_pbf

        pbf_path.write_bytes(b"\x00" * 10)
        monkeypatch.setattr("osm_bulk_import.shutil.which", lambda _name: None)

        # Should not raise
        update_pbf(pbf_path)
        # Exact log text: user can grep for it in CI runs.
        assert any("pyosmium-up-to-date" in rec.message for rec in caplog.records)
