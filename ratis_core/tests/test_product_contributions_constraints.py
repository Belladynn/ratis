"""Direct PG-level CHECK assertion tests for ``product_contributions`` — Pattern A.

Three CHECKs are pinned :

* ``ck_contributions_field``        : whitelist of accepted field names
* ``ck_contributions_value_shape``  : scalar field → value_text only ;
                                      array field → value_array only.
* ``ck_contributions_status``       : whitelist of accepted statuses.

These tests use raw INSERTs at the connection level so the CHECK fires
at ``flush()`` time, ensuring the model's ``__table_args__`` mirror what
PG enforces (Pattern A guard via ``test_schema_sync``).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _make_user(db: Any) -> uuid.UUID:
    from ratis_core.identifiers import generate_support_id

    uid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users "
            "    (id, email, support_id, account_type, "
            "     is_deleted, created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', false, now(), now())"
        ),
        {
            "id": uid,
            "email": f"u-{uid.hex[:8]}@example.com",
            "sid": generate_support_id(),
        },
    )
    return uid


def _insert_contribution(
    db: Any,
    *,
    user_id: uuid.UUID,
    field: str,
    value_text: str | None,
    value_array: list[str] | None,
    status: str = "applied",
) -> uuid.UUID:
    """Insert + flush a ``product_contributions`` row."""
    cid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO product_contributions "
            "    (id, user_id, product_ean, field, value_text, value_array, "
            "     status, created_at) "
            "VALUES (:id, :uid, :ean, :field, :vtext, :varr, :status, now())"
        ),
        {
            "id": cid,
            "uid": user_id,
            "ean": "3017620422003",
            "field": field,
            "vtext": value_text,
            "varr": value_array,
            "status": status,
        },
    )
    db.flush()
    return cid


# ============================================================
# ck_contributions_field
# ============================================================


def test_unknown_field_violates_ck_field(db):
    """``field='nutriscore'`` (not in whitelist) must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="nutriscore",
            value_text="A",
            value_array=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_field" in msg or "check constraint" in msg
    db.rollback()


# ============================================================
# ck_contributions_value_shape — scalar fields require value_text
# ============================================================


def test_brands_with_array_value_violates_shape(db):
    """``field='brands'`` with array payload must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="brands",
            value_text=None,
            value_array=["nutella"],
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_value_shape" in msg or "check constraint" in msg
    db.rollback()


def test_name_with_array_value_violates_shape(db):
    """``field='name'`` with array payload must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="name",
            value_text=None,
            value_array=["Nutella 400g"],
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_value_shape" in msg or "check constraint" in msg
    db.rollback()


def test_brands_without_value_text_violates_shape(db):
    """``field='brands'`` with both columns NULL must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="brands",
            value_text=None,
            value_array=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_value_shape" in msg or "check constraint" in msg
    db.rollback()


# ============================================================
# ck_contributions_value_shape — array fields require value_array
# ============================================================


def test_categories_tags_with_text_value_violates_shape(db):
    """``field='categories_tags'`` with scalar payload must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="categories_tags",
            value_text="en:dairies",
            value_array=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_value_shape" in msg or "check constraint" in msg
    db.rollback()


def test_labels_tags_with_text_value_violates_shape(db):
    """``field='labels_tags'`` with scalar payload must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="labels_tags",
            value_text="en:organic",
            value_array=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_value_shape" in msg or "check constraint" in msg
    db.rollback()


def test_categories_tags_with_both_columns_violates_shape(db):
    """Both value_text and value_array set → CHECK rejects."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="categories_tags",
            value_text="en:dairies",
            value_array=["en:dairies"],
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_value_shape" in msg or "check constraint" in msg
    db.rollback()


# ============================================================
# ck_contributions_status
# ============================================================


def test_unknown_status_violates_ck_status(db):
    """``status='draft'`` (not in whitelist) must be rejected."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_contribution(
            db,
            user_id=uid,
            field="brands",
            value_text="nutella",
            value_array=None,
            status="draft",
        )
    msg = str(exc_info.value.orig).lower()
    assert "ck_contributions_status" in msg or "check constraint" in msg
    db.rollback()


# ============================================================
# Happy paths — accepted shapes
# ============================================================


def test_brands_with_value_text_succeeds(db):
    uid = _make_user(db)
    cid = _insert_contribution(
        db,
        user_id=uid,
        field="brands",
        value_text="Nutella",
        value_array=None,
    )
    assert cid is not None
    db.rollback()


def test_name_with_value_text_succeeds(db):
    uid = _make_user(db)
    cid = _insert_contribution(
        db,
        user_id=uid,
        field="name",
        value_text="Nutella 400g",
        value_array=None,
    )
    assert cid is not None
    db.rollback()


def test_categories_tags_with_value_array_succeeds(db):
    uid = _make_user(db)
    cid = _insert_contribution(
        db,
        user_id=uid,
        field="categories_tags",
        value_text=None,
        value_array=["en:dairies", "en:cheeses"],
    )
    assert cid is not None
    db.rollback()


def test_labels_tags_with_value_array_succeeds(db):
    uid = _make_user(db)
    cid = _insert_contribution(
        db,
        user_id=uid,
        field="labels_tags",
        value_text=None,
        value_array=["en:organic"],
    )
    assert cid is not None
    db.rollback()


def test_status_pending_review_succeeds(db):
    uid = _make_user(db)
    cid = _insert_contribution(
        db,
        user_id=uid,
        field="brands",
        value_text="Nutella",
        value_array=None,
        status="pending_review",
    )
    assert cid is not None
    db.rollback()


def test_status_rejected_succeeds(db):
    uid = _make_user(db)
    cid = _insert_contribution(
        db,
        user_id=uid,
        field="brands",
        value_text="Nutella",
        value_array=None,
        status="rejected",
    )
    assert cid is not None
    db.rollback()
