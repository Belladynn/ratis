"""Tests for ``suggestions_service`` — tier composition for the default
search field empty state."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from ratis_core.models.product import Product
from ratis_core.models.scan import Scan
from ratis_core.models.shopping import ShoppingList, ShoppingListItem
from ratis_core.models.user import User
from services.suggestions_service import (
    _hydrate_with_products,
    _query_user_recent_eans,
    get_default_suggestions,
)
from sqlalchemy.orm import Session

# Test EANs : real 13-digit shape (constraint ``ean_format = '^\d{8,14}$'``)
# but using a private 9xx prefix to avoid colliding with the conftest's
# fixed-EAN ``product`` fixture (3017620422003 — Nutella) or real OFF rows.
EAN_111 = "9000000000111"
EAN_222 = "9000000000222"
EAN_AAA = "9000000000333"
EAN_BBB = "9000000000444"
EAN_CCC = "9000000000555"
# Curated-tier test EANs (9100 prefix to keep them distinct from the
# user-history EANs).
EAN_C1 = "9100000000001"
EAN_C2 = "9100000000002"
EAN_C3 = "9100000000003"
EAN_C4 = "9100000000004"
EAN_C5 = "9100000000005"
# Extra user-history EANs for the "user has > limit" scenario.
EAN_U1 = "9200000000001"
EAN_U2 = "9200000000002"
EAN_U3 = "9200000000003"
EAN_U4 = "9200000000004"
EAN_U5 = "9200000000005"
EAN_U6 = "9200000000006"


def _make_product(db: Session, ean: str, name: str = "X") -> Product:
    p = Product(ean=ean, name=name, source="off")
    db.add(p)
    return p


def _make_scan(
    db: Session,
    user,
    ean: str | None,
    status: str,
    scanned_at: datetime,
    *,
    match_method: str | None = None,
) -> Scan:
    """Build a minimal Scan row that satisfies all CHECK constraints.

    - ``price`` is NOT NULL → use a fixed dev value
    - ``store_status='unknown'`` when ``store_id`` is NULL (CHECK
      ``ck_scans_store_status_consistency``)
    - ``status='matched'`` requires ``match_method`` NOT NULL (CHECK
      ``ck_scans_matched_requires_ean_method``) — auto-fill if matched.
    """
    if status == "matched" and match_method is None:
        match_method = "manual"
    # ``scan_type='electronic_label'`` keeps receipt_id NULL (no parent
    # receipt row needed) and has no additional per-type CHECK like the
    # manual scan has (``manual_no_scanned_name``). It's the cheapest
    # valid Scan shape for this test bed.
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        scan_type="electronic_label",
        status=status,
        product_ean=ean,
        scanned_at=scanned_at,
        price=100,
        store_status="unknown",
        match_method=match_method,
    )
    db.add(s)
    return s


def _make_list_item(
    db: Session,
    user,
    ean: str,
    created_at: datetime,
) -> ShoppingListItem:
    lst = ShoppingList(
        id=uuid.uuid4(),
        user_id=user.id,
        name="",
        has_default_name=True,
        created_at=created_at,
    )
    db.add(lst)
    db.flush()
    item = ShoppingListItem(
        id=uuid.uuid4(),
        list_id=lst.id,
        product_ean=ean,
        created_at=created_at,
    )
    db.add(item)
    return item


def _make_user(db: Session, email: str) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=email,
        display_name=email.split("@")[0],
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    return u


def test_returns_empty_for_user_with_no_history(db, user):
    result = _query_user_recent_eans(db, user.id, limit=5)
    assert result == []


def test_returns_only_matched_scans(db, user):
    _make_product(db, EAN_111)
    _make_product(db, EAN_222)
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_111, "matched", now)
    _make_scan(db, user, EAN_222, "pending", now - timedelta(minutes=1))
    db.flush()
    result = _query_user_recent_eans(db, user.id, limit=5)
    assert result == [EAN_111]


def test_excludes_null_product_ean(db, user):
    """The SQL must not return NULL EANs.

    Note : we use ``status='pending'`` for the NULL-ean row because the
    CHECK constraint ``ck_scans_matched_requires_ean_method`` forbids
    ``status='matched' AND product_ean IS NULL``. The NULL-ean row is
    therefore excluded by BOTH the status filter and the EAN-is-NULL
    filter — but we still get coverage that the SQL doesn't crash on
    NULL EANs in the user's scans.
    """
    _make_product(db, EAN_111)
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_111, "matched", now)
    _make_scan(db, user, None, "pending", now - timedelta(minutes=1))
    db.flush()
    result = _query_user_recent_eans(db, user.id, limit=5)
    assert result == [EAN_111]


def test_unions_scans_and_list_items_dedupe_by_ean(db, user):
    _make_product(db, EAN_111)
    _make_product(db, EAN_222)
    now = datetime.now(UTC)
    # 111 from both sources — list_item more recent
    _make_scan(db, user, EAN_111, "matched", now - timedelta(days=2))
    _make_list_item(db, user, EAN_111, now)
    # 222 only in list
    _make_list_item(db, user, EAN_222, now - timedelta(days=1))
    db.flush()
    result = _query_user_recent_eans(db, user.id, limit=5)
    # 111 deduped, recency from list_item wins, sorted DESC
    assert result == [EAN_111, EAN_222]


def test_sorts_by_recency_desc(db, user):
    for ean in [EAN_AAA, EAN_BBB, EAN_CCC]:
        _make_product(db, ean)
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_AAA, "matched", now - timedelta(days=3))
    _make_scan(db, user, EAN_BBB, "matched", now - timedelta(days=1))
    _make_scan(db, user, EAN_CCC, "matched", now - timedelta(days=2))
    db.flush()
    result = _query_user_recent_eans(db, user.id, limit=5)
    assert result == [EAN_BBB, EAN_CCC, EAN_AAA]


def test_respects_limit(db, user):
    for ean in [EAN_AAA, EAN_BBB, EAN_CCC]:
        _make_product(db, ean)
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_AAA, "matched", now - timedelta(seconds=1))
    _make_scan(db, user, EAN_BBB, "matched", now - timedelta(seconds=2))
    _make_scan(db, user, EAN_CCC, "matched", now - timedelta(seconds=3))
    db.flush()
    result = _query_user_recent_eans(db, user.id, limit=2)
    assert result == [EAN_AAA, EAN_BBB]
    assert len(result) == 2


def test_isolates_users(db, user):
    other = _make_user(db, email="other@ratis.fr")
    _make_product(db, EAN_111)
    _make_product(db, EAN_222)
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_111, "matched", now)
    _make_scan(db, other, EAN_222, "matched", now)
    db.flush()
    assert _query_user_recent_eans(db, user.id, limit=5) == [EAN_111]
    assert _query_user_recent_eans(db, other.id, limit=5) == [EAN_222]


# ─────────────────────────────────────────────────────────────────────────────
# _hydrate_with_products
# ─────────────────────────────────────────────────────────────────────────────


def test_hydrate_returns_product_search_hits_in_order(db, user):
    _make_product(db, EAN_111, name="Lait")
    _make_product(db, EAN_222, name="Pain")
    _make_product(db, EAN_AAA, name="Oeufs")
    db.flush()
    hits = _hydrate_with_products(db, [EAN_222, EAN_111, EAN_AAA])
    assert [h["ean"] for h in hits] == [EAN_222, EAN_111, EAN_AAA]
    assert hits[0]["name"] == "Pain"


def test_hydrate_skips_missing_products(db, user, caplog):
    _make_product(db, EAN_111, name="Lait")
    db.flush()
    with caplog.at_level("WARNING"):
        hits = _hydrate_with_products(db, [EAN_111, "9999000000999"])
    assert [h["ean"] for h in hits] == [EAN_111]
    assert "9999000000999" in caplog.text  # WARN logged


def test_hydrate_empty_input_returns_empty(db, user):
    assert _hydrate_with_products(db, []) == []


# ─────────────────────────────────────────────────────────────────────────────
# get_default_suggestions
# ─────────────────────────────────────────────────────────────────────────────


def test_default_suggestions_user_with_zero_history(db, user):
    _make_product(db, EAN_C1, name="curated 1")
    _make_product(db, EAN_C2, name="curated 2")
    _make_product(db, EAN_C3, name="curated 3")
    _make_product(db, EAN_C4, name="curated 4")
    _make_product(db, EAN_C5, name="curated 5")
    db.flush()
    with patch(
        "services.suggestions_service.load_curated_eans",
        return_value=[EAN_C1, EAN_C2, EAN_C3, EAN_C4, EAN_C5],
    ):
        hits = get_default_suggestions(db, user.id, limit=5)
    assert [h["ean"] for h in hits] == [EAN_C1, EAN_C2, EAN_C3, EAN_C4, EAN_C5]


def test_default_suggestions_user_with_partial_history_tops_up_with_curated(
    db,
    user,
):
    _make_product(db, EAN_U1, name="user 1")
    _make_product(db, EAN_U2, name="user 2")
    _make_product(db, EAN_C1, name="curated 1")
    _make_product(db, EAN_C2, name="curated 2")
    _make_product(db, EAN_C3, name="curated 3")
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_U1, "matched", now)
    _make_scan(db, user, EAN_U2, "matched", now - timedelta(days=1))
    db.flush()
    with patch(
        "services.suggestions_service.load_curated_eans",
        return_value=[EAN_C1, EAN_C2, EAN_C3, EAN_C4, EAN_C5],
    ):
        hits = get_default_suggestions(db, user.id, limit=5)
    assert [h["ean"] for h in hits] == [EAN_U1, EAN_U2, EAN_C1, EAN_C2, EAN_C3]


def test_default_suggestions_user_with_full_history_no_curated(db, user):
    eans = [EAN_U1, EAN_U2, EAN_U3, EAN_U4, EAN_U5, EAN_U6]
    for ean in eans:
        _make_product(db, ean)
    now = datetime.now(UTC)
    for i, ean in enumerate(eans):
        _make_scan(db, user, ean, "matched", now - timedelta(seconds=i))
    db.flush()
    with patch(
        "services.suggestions_service.load_curated_eans",
        return_value=[EAN_C1, EAN_C2, EAN_C3],
    ):
        hits = get_default_suggestions(db, user.id, limit=5)
    assert [h["ean"] for h in hits] == [EAN_U1, EAN_U2, EAN_U3, EAN_U4, EAN_U5]


def test_default_suggestions_curated_dedupes_against_user_history(db, user):
    _make_product(db, EAN_AAA, name="shared")
    _make_product(db, EAN_C1, name="curated 1")
    _make_product(db, EAN_C2, name="curated 2")
    _make_product(db, EAN_C3, name="curated 3")
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_AAA, "matched", now)
    db.flush()
    with patch(
        "services.suggestions_service.load_curated_eans",
        # AAA in curated should be skipped (already in user history)
        return_value=[EAN_C1, EAN_AAA, EAN_C2, EAN_C3],
    ):
        hits = get_default_suggestions(db, user.id, limit=4)
    eans = [h["ean"] for h in hits]
    assert eans == [EAN_AAA, EAN_C1, EAN_C2, EAN_C3]
    assert eans.count(EAN_AAA) == 1
