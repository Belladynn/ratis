"""Tests for batch_shared.retailer_resolution — resolve_or_create_retailer().

Mirrors DA-34 behaviour (originally tested in
``batch/ratis_batch_osm_sync/tests/test_retailer_resolution.py``) but now via
the shared module that will be used by SIRENE + OSM (PR7).
"""

from __future__ import annotations

from ratis_core.seed.retailers import seed_retailers
from sqlalchemy import text


class TestResolveOrCreateRetailerShared:
    def test_resolve_known_alias(self, db):
        """A seeded alias resolves to the corresponding retailer_id."""
        from batch_shared.retailer_resolution import resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        retailer_id = resolve_or_create_retailer(db, "Lidl")
        assert retailer_id is not None
        row = db.execute(
            text("SELECT slug FROM retailers WHERE id = :rid"),
            {"rid": retailer_id},
        ).first()
        assert row is not None
        assert row.slug == "lidl"

    def test_resolve_case_insensitive(self, db):
        """Lookup is case-insensitive (alias stored as lowercase)."""
        from batch_shared.retailer_resolution import resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        id_upper = resolve_or_create_retailer(db, "CARREFOUR MARKET")
        db.flush()
        id_lower = resolve_or_create_retailer(db, "carrefour market")
        db.flush()

        assert id_upper is not None
        assert id_lower is not None
        assert id_upper == id_lower

    def test_resolve_creates_unknown(self, db):
        """An unknown brand name creates an unverified retailer with an alias."""
        from batch_shared.retailer_resolution import resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        retailer_id = resolve_or_create_retailer(db, "SuperEpicerie du Coin")
        assert retailer_id is not None
        row = db.execute(
            text("SELECT canonical_name, slug, is_verified FROM retailers WHERE id = :rid"),
            {"rid": retailer_id},
        ).first()
        assert row is not None
        assert row.canonical_name == "SuperEpicerie du Coin"
        assert row.slug == "superepicerie-du-coin"
        assert row.is_verified is False

        alias_row = db.execute(
            text("SELECT source FROM retailer_aliases WHERE retailer_id = :rid AND alias = 'superepicerie du coin'"),
            {"rid": retailer_id},
        ).first()
        assert alias_row is not None
        assert alias_row.source == "sirene"

    def test_resolve_creates_unknown_custom_source(self, db):
        """alias_source kwarg is stored on the auto-created alias row."""
        from batch_shared.retailer_resolution import resolve_or_create_retailer

        retailer_id = resolve_or_create_retailer(db, "MagasinX", alias_source="overture")
        db.flush()
        assert retailer_id is not None
        alias_row = db.execute(
            text("SELECT source FROM retailer_aliases WHERE retailer_id = :rid AND alias = 'magasinx'"),
            {"rid": retailer_id},
        ).first()
        assert alias_row is not None
        assert alias_row.source == "overture"

    def test_rerun_does_not_duplicate(self, db):
        """Calling resolve twice for the same name returns the same id."""
        from batch_shared.retailer_resolution import resolve_or_create_retailer

        seed_retailers(db)
        db.flush()

        rid1 = resolve_or_create_retailer(db, "BrandNew")
        db.flush()
        rid2 = resolve_or_create_retailer(db, "BrandNew")
        db.flush()
        rid3 = resolve_or_create_retailer(db, "brandnew")
        db.flush()

        assert rid1 == rid2 == rid3
        cnt = db.execute(text("SELECT COUNT(*) FROM retailers WHERE slug = 'brandnew'")).scalar()
        assert cnt == 1

    def test_resolve_none_when_no_name(self, db):
        """None or blank brand_name → None, no INSERT."""
        from batch_shared.retailer_resolution import resolve_or_create_retailer

        before = db.execute(text("SELECT COUNT(*) FROM retailers")).scalar()

        assert resolve_or_create_retailer(db, None) is None
        assert resolve_or_create_retailer(db, "") is None
        assert resolve_or_create_retailer(db, "   ") is None

        after = db.execute(text("SELECT COUNT(*) FROM retailers")).scalar()
        assert before == after, "No INSERT expected for None/blank brand_name"
