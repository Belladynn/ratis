"""Tests for retailer-aliasing in batch_osm_sync (DA-34)."""

from __future__ import annotations

from unittest.mock import patch

from ratis_core.seed.retailers import seed_retailers
from sqlalchemy import text


class TestResolveOrCreateRetailer:
    def test_known_alias_resolves_to_existing_retailer(self, db):
        from osm_sync import _resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        retailer_id = _resolve_or_create_retailer(db, "Lidl")
        assert retailer_id is not None
        row = db.execute(
            text("SELECT slug FROM retailers WHERE id = :rid"),
            {"rid": retailer_id},
        ).first()
        assert row.slug == "lidl"

    def test_case_insensitive_match(self, db):
        from osm_sync import _resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        retailer_id = _resolve_or_create_retailer(db, "CARREFOUR")
        assert retailer_id is not None
        row = db.execute(
            text("SELECT slug FROM retailers WHERE id = :rid"),
            {"rid": retailer_id},
        ).first()
        assert row.slug == "carrefour"

    def test_unknown_tag_auto_creates_unverified_retailer(self, db):
        from osm_sync import _resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        retailer_id = _resolve_or_create_retailer(db, "NewShop Express")
        assert retailer_id is not None
        row = db.execute(
            text("SELECT canonical_name, slug, is_verified FROM retailers WHERE id = :rid"),
            {"rid": retailer_id},
        ).first()
        assert row.canonical_name == "NewShop Express"
        assert row.slug == "newshop-express"
        assert row.is_verified is False

        alias_row = db.execute(
            text("SELECT source FROM retailer_aliases WHERE retailer_id = :rid AND alias = 'newshop express'"),
            {"rid": retailer_id},
        ).first()
        assert alias_row is not None
        assert alias_row.source == "osm"

    def test_rerun_does_not_duplicate(self, db):
        from osm_sync import _resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        rid1 = _resolve_or_create_retailer(db, "NewShop Express")
        db.flush()
        rid2 = _resolve_or_create_retailer(db, "NewShop Express")
        db.flush()
        rid3 = _resolve_or_create_retailer(db, "newshop express")  # same alias lower
        db.flush()

        assert rid1 == rid2 == rid3
        cnt = db.execute(text("SELECT COUNT(*) FROM retailers WHERE slug = 'newshop-express'")).scalar()
        assert cnt == 1

    def test_empty_tag_returns_none(self, db):
        from osm_sync import _resolve_or_create_retailer

        assert _resolve_or_create_retailer(db, "") is None
        assert _resolve_or_create_retailer(db, "   ") is None
        assert _resolve_or_create_retailer(db, None) is None


class TestRunBatchAssignsRetailerId:
    def test_store_gets_retailer_id_from_seed_alias(self, session_factory):
        from osm_sync import run_batch

        # Seed in its own transaction so run_batch can see the data.
        with session_factory() as db:
            seed_retailers(db)
            db.commit()

        mock_elements = [
            {
                "type": "node",
                "id": 60001,
                "lat": 48.86,
                "lon": 2.35,
                "tags": {
                    "name": "Lidl République",
                    "brand": "Lidl",
                    "addr:city": "Paris",
                    "addr:postcode": "75011",
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
            run_batch(session_factory, cfg, "http://fake", dry_run=False)

        with session_factory() as db:
            row = db.execute(
                text(
                    """
                    SELECT s.retailer, s.retailer_id, r.slug
                    FROM stores s LEFT JOIN retailers r ON r.id = s.retailer_id
                    WHERE s.osm_id = 60001
                    """
                )
            ).first()
            assert row is not None
            assert row.retailer_id is not None
            assert row.slug == "lidl"
            # Denormalized cache set by trigger.
            assert row.retailer == "Lidl"
