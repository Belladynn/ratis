"""Tests for ratis_core.seed.retailers (DA-34).

Seed must be :
- idempotent (rerunning inserts 0 new rows)
- parent-resolving (two-phase : rows first, then parent_id)
- alias-upserting (ON CONFLICT DO NOTHING on (retailer_id, alias))
"""

from __future__ import annotations

from sqlalchemy import text


class TestSeedRetailers:
    def test_first_run_inserts_rows(self, db):
        from ratis_core.seed.retailers import seed_retailers

        stats = seed_retailers(db)
        db.flush()

        assert stats["inserted"] >= 30, f"expected ≥30 retailers, got {stats['inserted']}"
        assert stats["aliases_added"] >= stats["inserted"]

        count = db.execute(text("SELECT COUNT(*) AS cnt FROM retailers")).scalar()
        assert count == stats["inserted"]

    def test_second_run_is_idempotent(self, db):
        from ratis_core.seed.retailers import seed_retailers

        seed_retailers(db)
        db.flush()
        count_before = db.execute(text("SELECT COUNT(*) FROM retailers")).scalar()
        aliases_before = db.execute(text("SELECT COUNT(*) FROM retailer_aliases")).scalar()

        stats = seed_retailers(db)
        db.flush()

        assert stats["inserted"] == 0, "idempotence broken — inserted > 0 on rerun"
        assert stats["aliases_added"] == 0

        count_after = db.execute(text("SELECT COUNT(*) FROM retailers")).scalar()
        aliases_after = db.execute(text("SELECT COUNT(*) FROM retailer_aliases")).scalar()
        assert count_after == count_before
        assert aliases_after == aliases_before

    def test_parent_relationships_resolved(self, db):
        from ratis_core.seed.retailers import seed_retailers

        seed_retailers(db)
        db.flush()

        # Carrefour Market should have parent = Carrefour.
        row = db.execute(
            text(
                """
                SELECT c.slug AS child_slug, p.slug AS parent_slug
                FROM retailers c
                JOIN retailers p ON p.id = c.parent_id
                WHERE c.slug = 'carrefour-market'
                """
            )
        ).first()
        assert row is not None
        assert row.parent_slug == "carrefour"

    def test_aliases_stored_lowercased(self, db):
        from ratis_core.seed.retailers import seed_retailers

        seed_retailers(db)
        db.flush()

        # Every alias must equal lower(alias).
        any_uppercase = db.execute(text("SELECT COUNT(*) FROM retailer_aliases WHERE alias <> lower(alias)")).scalar()
        assert any_uppercase == 0

    def test_alias_resolution_roundtrip(self, db):
        """A known alias must resolve to the expected canonical retailer."""
        from ratis_core.seed.retailers import seed_retailers

        seed_retailers(db)
        db.flush()

        row = db.execute(
            text(
                """
                SELECT r.slug
                FROM retailer_aliases a
                JOIN retailers r ON r.id = a.retailer_id
                WHERE a.alias = 'lidl'
                """
            )
        ).first()
        assert row is not None
        assert row.slug == "lidl"

    def test_color_hex_propagated_when_present(self, db):
        from ratis_core.seed.retailers import seed_retailers

        seed_retailers(db)
        db.flush()
        row = db.execute(text("SELECT color_hex FROM retailers WHERE slug = 'carrefour'")).first()
        assert row is not None
        assert row.color_hex == "#1D499F"
