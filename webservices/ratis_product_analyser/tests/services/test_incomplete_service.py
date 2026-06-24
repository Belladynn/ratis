"""Tests for incomplete_service — surfaces products with missing fields
ranked by cross-user popularity for the « Compléter ce produit » screen."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ratis_core.models.product import Product
from ratis_core.models.scan import Scan
from ratis_core.models.shopping import ShoppingList, ShoppingListItem
from ratis_core.models.user import User
from services.incomplete_service import _query_popular_incomplete_rows
from sqlalchemy.orm import Session

# Sentinel distinguishes "caller did not pass" (→ default value) from
# "caller passed None" (→ persist NULL). Without this, tests can't
# exercise the "missing field is NULL in DB" case.
_UNSET = object()


def _make_product(
    db: Session,
    ean: str,
    *,
    name: str = "Produit",
    brands=_UNSET,
    categories=_UNSET,
    labels=_UNSET,
    source: str = "off",
) -> Product:
    p = Product(
        ean=ean,
        name=name,
        brands_text="Brand" if brands is _UNSET else brands,
        categories_tags=["en:foods"] if categories is _UNSET else categories,
        labels_tags=["en:organic"] if labels is _UNSET else labels,
        source=source,
    )
    db.add(p)
    return p


def _make_user(db: Session, email: str) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=email,
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_scan(db: Session, user, ean: str, status: str) -> Scan:
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        scan_type="electronic_label",
        status=status,
        product_ean=ean,
        scanned_at=datetime.now(UTC),
        store_status="unknown",
        price=100,
        match_method="manual" if status == "matched" else None,
    )
    db.add(s)
    return s


def _make_list_item(db: Session, user, ean: str) -> ShoppingListItem:
    lst = ShoppingList(
        id=uuid.uuid4(),
        user_id=user.id,
        name="",
        has_default_name=True,
    )
    db.add(lst)
    db.flush()
    item = ShoppingListItem(
        id=uuid.uuid4(),
        list_id=lst.id,
        product_ean=ean,
    )
    db.add(item)
    return item


def test_returns_empty_when_all_products_complete(db):
    _make_product(db, "9990000000001")  # all fields set
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    assert rows == []


def test_returns_product_with_missing_brands(db):
    _make_product(db, "9990000000001", brands=None)
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    assert len(rows) == 1
    assert rows[0].ean == "9990000000001"
    assert rows[0].brands_text is None


def test_returns_product_with_empty_categories_array(db):
    _make_product(db, "9990000000001", categories=[])
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    assert len(rows) == 1


def test_returns_product_with_null_categories(db):
    _make_product(db, "9990000000001", categories=None)
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    assert len(rows) == 1


def test_orders_by_popularity_desc(db):
    user = _make_user(db, "u1@ratis.fr")
    _make_product(db, "9990000000001", brands=None)
    _make_product(db, "9990000000002", brands=None)
    _make_product(db, "9990000000003", brands=None)
    # product 2 is most popular (3 scans)
    for _ in range(3):
        _make_scan(db, user, "9990000000002", "matched")
    # product 1 has 1 scan
    _make_scan(db, user, "9990000000001", "matched")
    # product 3 has 0 popularity
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    assert [r.ean for r in rows[:2]] == ["9990000000002", "9990000000001"]


def test_counts_list_items_in_popularity(db):
    user = _make_user(db, "u1@ratis.fr")
    _make_product(db, "9990000000001", brands=None)
    _make_product(db, "9990000000002", brands=None)
    # product 1 : 2 scans + 0 list items
    _make_scan(db, user, "9990000000001", "matched")
    _make_scan(db, user, "9990000000001", "matched")
    # product 2 : 0 scans + 3 list items
    _make_list_item(db, user, "9990000000002")
    _make_list_item(db, user, "9990000000002")
    _make_list_item(db, user, "9990000000002")
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    assert [r.ean for r in rows[:2]] == ["9990000000002", "9990000000001"]


def test_excludes_pending_scans_from_popularity(db):
    user = _make_user(db, "u1@ratis.fr")
    _make_product(db, "9990000000001", brands=None)
    _make_product(db, "9990000000002", brands=None)
    # product 1 : 1 matched scan
    _make_scan(db, user, "9990000000001", "matched")
    # product 2 : 5 pending scans (excluded from popularity)
    for _ in range(5):
        _make_scan(db, user, "9990000000002", "pending")
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=10)
    # product 1 must come first despite product 2 having more scans
    assert rows[0].ean == "9990000000001"


def test_respects_limit(db):
    for i in range(5):
        _make_product(db, f"999000000000{i + 1}", brands=None)
    db.commit()
    rows = _query_popular_incomplete_rows(db, limit=2)
    assert len(rows) == 2


# ─────────────────────────────────────────────────────────────────────────────
# _pick_missing_field
# ─────────────────────────────────────────────────────────────────────────────


from services.incomplete_service import _pick_missing_field


def _make_row(**kwargs):
    """Build a fake row matching the SQL output columns."""

    class Row:
        pass

    r = Row()
    r.ean = kwargs.get("ean", "9990000000001")
    r.name = kwargs.get("name", "Produit")
    r.brands_text = kwargs.get("brands_text", "Brand")
    r.categories_tags = kwargs.get("categories_tags", ["en:foods"])
    r.labels_tags = kwargs.get("labels_tags", ["en:organic"])
    return r


def test_pick_missing_field_brands_priority():
    row = _make_row(brands_text=None, categories_tags=None, labels_tags=None)
    assert _pick_missing_field(row) == "brands"


def test_pick_missing_field_empty_brands_string_is_missing():
    row = _make_row(brands_text="", categories_tags=["x"], labels_tags=["y"])
    assert _pick_missing_field(row) == "brands"


def test_pick_missing_field_categories_when_brands_set():
    row = _make_row(brands_text="X", categories_tags=None, labels_tags=None)
    assert _pick_missing_field(row) == "categories_tags"


def test_pick_missing_field_empty_categories_array():
    row = _make_row(brands_text="X", categories_tags=[], labels_tags=["y"])
    assert _pick_missing_field(row) == "categories_tags"


def test_pick_missing_field_labels_last():
    row = _make_row(brands_text="X", categories_tags=["c"], labels_tags=None)
    assert _pick_missing_field(row) == "labels_tags"


def test_pick_missing_field_empty_labels_array():
    row = _make_row(brands_text="X", categories_tags=["c"], labels_tags=[])
    assert _pick_missing_field(row) == "labels_tags"


def test_pick_missing_field_returns_none_when_all_set():
    row = _make_row()
    assert _pick_missing_field(row) is None


# ─────────────────────────────────────────────────────────────────────────────
# list_incomplete_products
# ─────────────────────────────────────────────────────────────────────────────


from unittest.mock import patch

from services.incomplete_service import list_incomplete_products


def test_list_incomplete_returns_enrichissement_task_shape(db):
    _make_product(db, "9990000000001", brands=None)
    db.commit()
    with patch(
        "services.incomplete_service.load_settings",
        return_value={"rewards": {"cab_per_fill_product_field": 5}},
    ):
        result = list_incomplete_products(db, limit=10)
    assert len(result) == 1
    task = result[0]
    assert set(task.keys()) == {
        "product_ean",
        "product_name",
        "missing_field",
        "cab_reward",
    }
    assert task["product_ean"] == "9990000000001"
    assert task["product_name"] == "Produit"
    assert task["missing_field"] == "brands"
    assert task["cab_reward"] == 5


def test_list_incomplete_uniform_reward_across_tasks(db):
    _make_product(db, "9990000000001", brands=None)
    _make_product(db, "9990000000002", categories=None)
    _make_product(db, "9990000000003", labels=None)
    db.commit()
    with patch(
        "services.incomplete_service.load_settings",
        return_value={"rewards": {"cab_per_fill_product_field": 7}},
    ):
        result = list_incomplete_products(db, limit=10)
    assert len(result) == 3
    for task in result:
        assert task["cab_reward"] == 7


def test_list_incomplete_skips_rows_where_pick_returns_none(db):
    """Safety net : if the SQL ever returns a row with all fields set
    (invariant break), the service skips it gracefully."""
    _make_product(db, "9990000000001", brands="X", categories=["c"], labels=["l"])
    # This product is complete — the SQL WHERE should exclude it. Test
    # that even if some upstream change leaked a complete row, we drop it.
    _make_product(db, "9990000000002", brands=None)
    db.commit()
    with patch(
        "services.incomplete_service.load_settings",
        return_value={"rewards": {"cab_per_fill_product_field": 5}},
    ):
        result = list_incomplete_products(db, limit=10)
    # Only the actually-incomplete one
    eans = {t["product_ean"] for t in result}
    assert eans == {"9990000000002"}


def test_list_incomplete_default_limit_10(db):
    for i in range(15):
        _make_product(db, f"99900000000{i + 10}", brands=None)
    db.commit()
    with patch(
        "services.incomplete_service.load_settings",
        return_value={"rewards": {"cab_per_fill_product_field": 5}},
    ):
        result = list_incomplete_products(db)
    assert len(result) == 10
