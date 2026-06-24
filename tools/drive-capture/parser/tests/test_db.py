"""Tests for the SQLite persistence layer."""

from parser.db import connect, init_schema, insert_observations, upsert_stores
from parser.model import ParsedProduct, ParsedStore


def _make_conn():
    conn = connect(":memory:")
    init_schema(conn)
    return conn


def test_init_schema_creates_tables():
    conn = _make_conn()
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"observations", "stores"} <= names


def test_insert_observations_is_append_only():
    conn = _make_conn()
    product = ParsedProduct(
        enseigne="carrefour",
        name="Test product",
        captured_at="2026-05-16T10:00:00",
        ean="3181232180801",
        price_cents=739,
        is_promo=False,
    )
    assert insert_observations(conn, [product]) == 1
    assert insert_observations(conn, [product]) == 1
    # two runs of the same product -> two rows (price history)
    count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert count == 2


def test_insert_observations_stores_bool_as_int():
    conn = _make_conn()
    product = ParsedProduct(
        enseigne="carrefour",
        name="Promo item",
        captured_at="2026-05-16T10:00:00",
        is_promo=True,
        available=True,
    )
    insert_observations(conn, [product])
    row = conn.execute(
        "SELECT is_promo, available FROM observations"
    ).fetchone()
    assert row["is_promo"] == 1
    assert row["available"] == 1


def test_upsert_stores_updates_on_conflict():
    conn = _make_conn()
    store_v1 = ParsedStore(
        enseigne="carrefour", store_ref="1323", name="Old name", city="Paris"
    )
    store_v2 = ParsedStore(
        enseigne="carrefour",
        store_ref="1323",
        name="Market Courbevoie",
        city="Courbevoie",
    )
    upsert_stores(conn, [store_v1])
    upsert_stores(conn, [store_v2])
    rows = conn.execute("SELECT name, city FROM stores").fetchall()
    assert len(rows) == 1  # upsert, not append
    assert rows[0]["name"] == "Market Courbevoie"
    assert rows[0]["city"] == "Courbevoie"
