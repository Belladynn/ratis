"""Tests for off_sync.repository — DB integration (real PG via conftest)."""

from off_sync.repository import upsert_products
from off_sync.sources import get_source
from sqlalchemy import text

_OFF = get_source("off")
_OBP = get_source("obp")


def _p(ean: str, name: str, photo_url: str | None = None, **kwargs) -> dict:
    """Minimal product dict matching the full _SYNC_COLS shape."""
    return {
        "ean": ean,
        "name": name,
        "photo_url": photo_url,
        "product_quantity": None,
        "product_quantity_unit": None,
        "quantity_raw": None,
        "storage_type": None,
        "allergens_tags": [],
        "ingredients_tags": [],
        "categories_tags": [],
        "labels_tags": [],
        # Phase C-2 — origins_tags drives the attribute:french qualifier emit.
        "origins_tags": [],
        "brands": None,
        "photo_url_small": None,
        # OFF multi-field enrichment columns (PR feat/off-sync-multi-fields).
        "product_name_fr": None,
        "generic_name_fr": None,
        "brands_text": None,
        "quantity_text": None,
        **kwargs,
    }


def test_upsert_empty_list(session_factory):
    with session_factory() as db:
        result = upsert_products(db, [], source=_OFF)
    assert result == (0, 0, 0)


def test_upsert_inserts_new_product(session_factory):
    with session_factory() as db:
        inserted, updated, skipped = upsert_products(db, [_p("3017620422003", "Nutella")], source=_OFF)
        db.commit()
    assert (inserted, updated, skipped) == (1, 0, 0)


def test_upsert_updates_existing_off_product(session_factory):
    ean = "3017620422004"
    with session_factory() as db:
        db.execute(
            text("INSERT INTO products (ean, name, source) VALUES (:e, :n, 'off')"),
            {"e": ean, "n": "Old Name"},
        )
        db.commit()

    with session_factory() as db:
        inserted, updated, skipped = upsert_products(db, [_p(ean, "New Name")], source=_OFF)
        db.commit()
    assert (inserted, updated, skipped) == (0, 1, 0)


def test_upsert_skips_internal_product(session_factory):
    """OFF data must never overwrite source='internal' products (vrac)."""
    ean = "2000000000001"
    with session_factory() as db:
        db.execute(
            text("INSERT INTO products (ean, name, source, unit) VALUES (:e, :n, 'internal', 'kg')"),
            {"e": ean, "n": "Vrac farine"},
        )
        db.commit()

    with session_factory() as db:
        inserted, updated, skipped = upsert_products(db, [_p(ean, "OFF Override")], source=_OFF)
        db.commit()
    assert (inserted, updated, skipped) == (0, 0, 1)

    with session_factory() as db:
        row = db.execute(text("SELECT name FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Vrac farine"


def test_upsert_mixed_batch(session_factory):
    """Batch with new + update + skip all counted correctly."""
    existing_off_ean = "3017620422010"
    internal_ean = "2000000000002"
    new_ean = "3017620422011"

    with session_factory() as db:
        db.execute(
            text("INSERT INTO products (ean, name, source) VALUES (:e, :n, 'off')"),
            {"e": existing_off_ean, "n": "Existing"},
        )
        db.execute(
            text("INSERT INTO products (ean, name, source, unit) VALUES (:e, :n, 'internal', 'l')"),
            {"e": internal_ean, "n": "Vrac huile"},
        )
        db.commit()

    products = [
        _p(new_ean, "New"),
        _p(existing_off_ean, "Updated"),
        _p(internal_ean, "Override"),
    ]
    with session_factory() as db:
        inserted, updated, skipped = upsert_products(db, products, source=_OFF)
        db.commit()

    assert (inserted, updated, skipped) == (1, 1, 1)


def test_upsert_updates_photo_url(session_factory):
    """photo_url is updated on re-sync."""
    ean = "3017620422005"
    with session_factory() as db:
        db.execute(
            text("INSERT INTO products (ean, name, source, photo_url) VALUES (:e, :n, 'off', :p)"),
            {"e": ean, "n": "Produit", "p": "http://old.jpg"},
        )
        db.commit()

    with session_factory() as db:
        upsert_products(db, [_p(ean, "Produit", "http://new.jpg")], source=_OFF)
        db.commit()

    with session_factory() as db:
        row = db.execute(text("SELECT photo_url FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.photo_url == "http://new.jpg"


def test_upsert_stores_new_fields(session_factory):
    """New columns are written and readable back."""
    ean = "3017620422060"
    product = _p(
        ean,
        "Légumes surgelés",
        product_quantity=500.0,
        product_quantity_unit="g",
        quantity_raw="500 g",
        storage_type="frozen",
        allergens_tags=[],
        ingredients_tags=["en:peas", "en:carrots"],
    )
    with session_factory() as db:
        upsert_products(db, [product], source=_OFF)
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT product_quantity, product_quantity_unit, quantity_raw, "
                "storage_type, ingredients_tags FROM products WHERE ean = :e"
            ),
            {"e": ean},
        ).one()
    assert float(row.product_quantity) == 500.0
    assert row.product_quantity_unit == "g"
    assert row.quantity_raw == "500 g"
    assert row.storage_type == "frozen"
    assert list(row.ingredients_tags) == ["en:peas", "en:carrots"]


def test_upsert_stores_multi_field_enrichment(session_factory):
    """OFF multi-field columns (product_name_fr, generic_name_fr, …) round-trip."""
    ean = "7610113013175"
    product = _p(
        ean,
        "Hipro +",
        product_name_fr="Hipro + protéines fraise",
        generic_name_fr="Yaourt à boire saveur fraise",
        brands_text="Hipro,Danone",
        quantity_text="4 x 250 g",
    )
    with session_factory() as db:
        upsert_products(db, [product], source=_OFF)
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text("SELECT product_name_fr, generic_name_fr, brands_text, quantity_text FROM products WHERE ean = :e"),
            {"e": ean},
        ).one()
    assert row.product_name_fr == "Hipro + protéines fraise"
    assert row.generic_name_fr == "Yaourt à boire saveur fraise"
    assert row.brands_text == "Hipro,Danone"
    assert row.quantity_text == "4 x 250 g"


def test_upsert_overwrites_multi_fields_on_resync(session_factory):
    """A second upsert refreshes the multi-fields (no stale values)."""
    ean = "7610113013176"
    with session_factory() as db:
        upsert_products(db, [_p(ean, "Old", product_name_fr="Old FR")], source=_OFF)
        db.commit()

    with session_factory() as db:
        upsert_products(
            db,
            [_p(ean, "New", product_name_fr="New FR", generic_name_fr="Generic")],
            source=_OFF,
        )
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text("SELECT product_name_fr, generic_name_fr FROM products WHERE ean = :e"),
            {"e": ean},
        ).one()
    assert row.product_name_fr == "New FR"
    assert row.generic_name_fr == "Generic"


# ── multi-source isolation ────────────────────────────────────────────────────


def test_upsert_inserts_with_source_obp(session_factory):
    """OBP upsert writes products.source = 'obp'."""
    with session_factory() as db:
        upsert_products(db, [_p("3000000000999", "Shampooing test")], source=_OBP)
        db.commit()

    with session_factory() as db:
        row = db.execute(text("SELECT source FROM products WHERE ean = :e"), {"e": "3000000000999"}).one()
    assert row.source == "obp"


def test_upsert_obp_does_not_overwrite_off_row(session_factory):
    """A product already classified by OFF must NOT be overwritten by OBP."""
    ean = "3017620499999"
    with session_factory() as db:
        db.execute(
            text("INSERT INTO products (ean, name, source) VALUES (:e, :n, 'off')"),
            {"e": ean, "n": "Existing OFF row"},
        )
        db.commit()

    with session_factory() as db:
        inserted, updated, skipped = upsert_products(db, [_p(ean, "OBP override")], source=_OBP)
        db.commit()
    assert (inserted, updated, skipped) == (0, 0, 1)

    with session_factory() as db:
        row = db.execute(text("SELECT name, source FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Existing OFF row"
    assert row.source == "off"


def test_upsert_obp_updates_existing_obp_row(session_factory):
    """Re-running OBP on its own rows updates them (idempotent)."""
    ean = "3000000000888"
    with session_factory() as db:
        upsert_products(db, [_p(ean, "Old OBP")], source=_OBP)
        db.commit()

    with session_factory() as db:
        inserted, updated, skipped = upsert_products(db, [_p(ean, "New OBP")], source=_OBP)
        db.commit()
    assert (inserted, updated, skipped) == (0, 1, 0)


# ── Phase C-2 — origins_tags round-trip ───────────────────────────────


def test_upsert_stores_origins_tags(session_factory):
    """``origins_tags`` round-trips through INSERT + the EXCLUDED clause
    on subsequent UPDATE. Phase C-2 contract — populated by every
    nightly off_sync run thereafter."""
    ean = "3017620499001"
    with session_factory() as db:
        upsert_products(
            db,
            [_p(ean, "Produit FR", origins_tags=["en:france"])],
            source=_OFF,
        )
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text("SELECT origins_tags FROM products WHERE ean = :e"),
            {"e": ean},
        ).one()
    assert list(row.origins_tags) == ["en:france"]


def test_upsert_overwrites_origins_tags_on_resync(session_factory):
    """A second upsert refreshes ``origins_tags`` (EXCLUDED clause) —
    catches the case where the OFF tag list evolves over time."""
    ean = "3017620499002"
    with session_factory() as db:
        upsert_products(
            db,
            [_p(ean, "Old", origins_tags=["en:germany"])],
            source=_OFF,
        )
        db.commit()

    with session_factory() as db:
        upsert_products(
            db,
            [
                _p(
                    ean,
                    "New",
                    origins_tags=[
                        "en:france",
                        "en:european-union",
                    ],
                )
            ],
            source=_OFF,
        )
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text("SELECT origins_tags FROM products WHERE ean = :e"),
            {"e": ean},
        ).one()
    assert list(row.origins_tags) == ["en:france", "en:european-union"]


def test_upsert_stores_empty_origins_tags(session_factory):
    """Empty list (OFF row without origin signal) round-trips as an
    empty ARRAY, NOT as NULL — keeps the post-extract default semantics
    consistent with allergens_tags / ingredients_tags."""
    ean = "3017620499003"
    with session_factory() as db:
        upsert_products(
            db,
            [_p(ean, "Produit sans origine", origins_tags=[])],
            source=_OFF,
        )
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text("SELECT origins_tags FROM products WHERE ean = :e"),
            {"e": ean},
        ).one()
    assert list(row.origins_tags) == []
