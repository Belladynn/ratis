"""Integration tests for vrac_seed.py — uses SA 2.0 SAVEPOINT fixtures."""

from __future__ import annotations

from seed_data import SEED_DATA, VracEntry
from sqlalchemy import text
from vrac_seed import seed_products


def _count_internal_products(db) -> int:
    return db.execute(text("SELECT count(*) FROM products WHERE source = 'internal'")).scalar_one()


class TestSeedProducts:
    def test_seed_inserts_all_when_db_empty(self, db):
        stats = seed_products(db, SEED_DATA)
        assert stats["inserted"] == len(SEED_DATA)
        assert stats["skipped"] == 0
        assert _count_internal_products(db) == len(SEED_DATA)

    def test_seed_idempotent_on_rerun(self, db):
        first = seed_products(db, SEED_DATA)
        assert first["inserted"] == len(SEED_DATA)

        second = seed_products(db, SEED_DATA)
        assert second["inserted"] == 0
        assert second["skipped"] == len(SEED_DATA)
        # No duplicates created.
        assert _count_internal_products(db) == len(SEED_DATA)

    def test_seed_skips_when_ean_exists(self, db):
        existing = SEED_DATA[0]
        db.execute(
            text("INSERT INTO products (ean, name, source, unit) VALUES (:ean, 'pre-existing name', 'internal', 'kg')"),
            {"ean": existing["ean"]},
        )
        db.flush()

        stats = seed_products(db, SEED_DATA)
        assert stats["inserted"] == len(SEED_DATA) - 1
        assert stats["skipped"] == 1

        # The original row's name is preserved (DO NOTHING — no overwrite).
        kept_name = db.execute(
            text("SELECT name FROM products WHERE ean = :ean"),
            {"ean": existing["ean"]},
        ).scalar_one()
        assert kept_name == "pre-existing name"

    def test_seed_dry_run_does_not_write(self, db):
        stats = seed_products(db, SEED_DATA, dry_run=True)
        assert stats["inserted"] == len(SEED_DATA)
        assert stats["skipped"] == 0
        # Nothing was actually written.
        assert _count_internal_products(db) == 0

    def test_seed_dry_run_reports_existing_as_skipped(self, db):
        existing = SEED_DATA[0]
        db.execute(
            text("INSERT INTO products (ean, name, source, unit) VALUES (:ean, 'pre-existing', 'internal', 'kg')"),
            {"ean": existing["ean"]},
        )
        db.flush()

        stats = seed_products(db, SEED_DATA, dry_run=True)
        assert stats["inserted"] == len(SEED_DATA) - 1
        assert stats["skipped"] == 1

    def test_seed_inserts_partial_subset(self, db):
        subset: list[VracEntry] = SEED_DATA[:5]
        stats = seed_products(db, subset)
        assert stats["inserted"] == 5
        # Subsequent run with full list inserts only the remainder.
        rest = seed_products(db, SEED_DATA)
        assert rest["inserted"] == len(SEED_DATA) - 5
        assert rest["skipped"] == 5
