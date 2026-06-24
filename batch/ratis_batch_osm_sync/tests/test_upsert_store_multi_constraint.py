"""Tests for ``normalize.upsert_store`` multi-constraint conflict handling.

Context : the ``stores`` table carries two unique invariants beyond the
canonical ``osm_id`` PK :

  - ``uq_stores_siret``  — partial UNIQUE on siret WHERE siret IS NOT NULL
  - ``unique_store``     — composite UNIQUE on
                           (COALESCE(retailer,''),
                            COALESCE(address,''),
                            COALESCE(postal_code,''))

PostgreSQL ``ON CONFLICT`` only accepts a single conflict_target per INSERT,
so the upsert_store function pre-checks the auxiliary uniqueness invariant
(siret) in Python before issuing the INSERT, NULL-ing out a conflicting
siret value and merging brand+address+postal_code collisions onto the
existing row rather than inserting a duplicate.

Note (2026-04-27) : phone is no longer unique. Multiple stores legitimately
share a corporate standard phone (franchise enseigne), so duplicates are
allowed and the ``uq_stores_phone`` index has been dropped. Tests cover the
new "duplicate phones preserved" behaviour (TestUpsertStorePhoneDuplicates).
See refactor/phone-as-retailer-signal.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text


def _base_store(osm_id: int, **overrides) -> dict:
    data = {
        "osm_id": osm_id,
        "name": f"Store {osm_id}",
        "retailer": None,
        "address": None,
        "city": None,
        "postal_code": None,
        "lat": Decimal("48.0"),
        "lng": Decimal("2.0"),
        "phone": None,
        "siret": None,
        "opening_hours": None,
    }
    data.update(overrides)
    return data


class TestUpsertStorePhoneDuplicates:
    """Phone is a *retailer signal*, not a store id (2026-04-27).

    Several franchise stores share the same corporate standard phone, so
    upsert_store must allow duplicate phones across distinct osm_ids rather
    than NULL-ing them out. See refactor/phone-as-retailer-signal.
    """

    def test_upsert_store_keeps_duplicate_phones(self, db):
        """Two stores sharing a phone (different osm_ids) must both keep it.

        Regression vs. the previous behaviour (`test_phone_conflict_nulls_
        phone_on_new_row`) — phone is no longer unique, so the second insert
        must NOT have its phone NULL'd. Same retailer, distinct addresses to
        also exercise the franchise-store scenario without colliding on
        ``unique_store``.
        """
        from normalize import upsert_store

        upsert_store(
            db,
            _base_store(
                80001,
                phone="0145678901",
                name="Carrefour Marseille",
                retailer="Carrefour",
                address="1 rue First",
                postal_code="13001",
            ),
        )
        db.flush()

        upsert_store(
            db,
            _base_store(
                80002,
                phone="0145678901",
                name="Carrefour Lille",
                retailer="Carrefour",
                address="2 rue Second",
                postal_code="59000",
            ),
        )
        db.flush()

        rows = db.execute(
            text("SELECT osm_id, name, phone FROM stores WHERE osm_id IN (80001, 80002) ORDER BY osm_id")
        ).all()
        assert len(rows) == 2
        first, second = rows
        assert first.phone == "0145678901"
        # Phone preserved on the second row — duplicates allowed.
        assert second.phone == "0145678901"
        assert second.name == "Carrefour Lille"

    def test_same_osm_id_keeps_phone_on_update(self, db):
        """Re-upserting the SAME osm_id with the same phone is not a conflict
        (the existing row IS the row holding that phone) — phone preserved.
        """
        from normalize import upsert_store

        upsert_store(
            db,
            _base_store(
                80010,
                phone="0145000111",
                name="Initial",
                address="3 rue Re",
                postal_code="75003",
            ),
        )
        db.flush()
        upsert_store(
            db,
            _base_store(
                80010,
                phone="0145000111",
                name="Renamed",
                address="3 rue Re",
                postal_code="75003",
            ),
        )
        db.flush()

        row = db.execute(text("SELECT name, phone FROM stores WHERE osm_id = 80010")).first()
        assert row.name == "Renamed"
        assert row.phone == "0145000111"


class TestUpsertStoreSiretConflict:
    def test_siret_conflict_nulls_siret_on_new_row(self, db):
        from normalize import upsert_store

        upsert_store(
            db,
            _base_store(
                80101,
                siret="55208329700374",
                name="A",
                address="10 rue A",
                postal_code="69001",
            ),
        )
        db.flush()

        upsert_store(
            db,
            _base_store(
                80102,
                siret="55208329700374",
                name="B",
                address="20 rue B",
                postal_code="69002",
            ),
        )
        db.flush()

        rows = db.execute(
            text("SELECT osm_id, name, siret FROM stores WHERE osm_id IN (80101, 80102) ORDER BY osm_id")
        ).all()
        assert len(rows) == 2
        a, b = rows
        assert a.siret == "55208329700374"
        assert b.name == "B"
        assert b.siret is None


class TestUpsertStoreCompositeConflict:
    def test_brand_address_postal_conflict_updates_existing_row(self, db):
        """When (retailer, address, postal_code) collides with a pre-existing
        row that has osm_id IS NULL (e.g. admin-seeded), the existing row is
        updated in place and adopts the new osm_id — no duplicate inserted.
        """
        from normalize import upsert_store

        # Pre-existing admin-sourced row (no osm_id) representing the same
        # physical store.
        db.execute(
            text(
                """
                INSERT INTO stores
                  (name, retailer, address, postal_code, lat, lng, is_disabled, source)
                VALUES
                  ('Admin Seeded', 'Carrefour', '12 rue Foo', '75001',
                   48.86, 2.35, false, 'admin')
                """
            )
        )
        db.flush()

        upsert_store(
            db,
            _base_store(
                80201,
                name="OSM Carrefour",
                retailer="Carrefour",
                address="12 rue Foo",
                postal_code="75001",
                lat=Decimal("48.86"),
                lng=Decimal("2.35"),
            ),
        )
        db.flush()

        # Exactly one row for that retailer+address+postal — the seeded one,
        # now adopting the OSM id and the OSM name.
        rows = db.execute(
            text(
                """
                SELECT osm_id, name FROM stores
                WHERE retailer = 'Carrefour'
                  AND address = '12 rue Foo'
                  AND postal_code = '75001'
                """
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].osm_id == 80201
        assert rows[0].name == "OSM Carrefour"

    def test_brand_address_postal_conflict_with_other_osm_id_skips(self, db, caplog):
        """Two distinct OSM nodes claiming the same (retailer, address,
        postal_code) — keep the first one already linked to its osm_id, skip
        the second to avoid silently overwriting its identity.
        """
        import logging

        from normalize import upsert_store

        upsert_store(
            db,
            _base_store(
                80301,
                name="First",
                retailer="Lidl",
                address="1 rue X",
                postal_code="69000",
            ),
        )
        db.flush()

        with caplog.at_level(logging.WARNING, logger="normalize"):
            upsert_store(
                db,
                _base_store(
                    80302,
                    name="Second",
                    retailer="Lidl",
                    address="1 rue X",
                    postal_code="69000",
                ),
            )
            db.flush()

        # Only the first row should exist for that key.
        rows = db.execute(
            text(
                "SELECT osm_id, name FROM stores "
                "WHERE retailer = 'Lidl' AND address = '1 rue X' "
                "AND postal_code = '69000'"
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].osm_id == 80301
        assert rows[0].name == "First"
        assert any("unique_store" in rec.message and "80302" in rec.message for rec in caplog.records), (
            "expected a WARNING mentioning unique_store and the skipped osm_id"
        )


class TestUpsertStoreNoConflict:
    def test_happy_path_inserts_normally(self, db):
        from normalize import upsert_store

        upsert_store(
            db,
            _base_store(
                80401,
                name="Clean",
                phone="0299110011",
                siret="12345678901234",
                retailer="Auchan",
                address="2 rue Bar",
                postal_code="35000",
            ),
        )
        db.flush()
        row = db.execute(
            text("SELECT name, phone, siret, retailer, address, postal_code FROM stores WHERE osm_id = 80401")
        ).first()
        assert row is not None
        assert row.name == "Clean"
        assert row.phone == "0299110011"
        assert row.siret == "12345678901234"
        assert row.retailer == "Auchan"
        assert row.address == "2 rue Bar"
        assert row.postal_code == "35000"


class TestUpsertStoreOsmIdConflict:
    """Pre-existing behaviour : same osm_id → UPDATE existing row in-place."""

    def test_osm_id_conflict_updates_in_place(self, db):
        from normalize import upsert_store

        upsert_store(
            db,
            _base_store(
                80501,
                name="Old",
                phone="0188776655",
                address="50 rue Old",
                postal_code="35000",
            ),
        )
        db.flush()
        upsert_store(
            db,
            _base_store(
                80501,
                name="New",
                phone="0188776655",
                address="50 rue Old",
                postal_code="35000",
            ),
        )
        db.flush()

        rows = db.execute(text("SELECT name, phone FROM stores WHERE osm_id = 80501")).all()
        assert len(rows) == 1
        assert rows[0].name == "New"
        assert rows[0].phone == "0188776655"
