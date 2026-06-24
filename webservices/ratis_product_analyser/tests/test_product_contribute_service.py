"""Unit tests for ``services/product_contribute_service.py`` (Phase C-5).

Route-level tests in ``test_product_contribute.py`` exercise the full
HTTP stack ; these tests pin the service contract independently :
validation matrix, apply-vs-queue decision, idempotency window,
trigger_action emit shape.
"""

from __future__ import annotations

import uuid

import pytest
from ratis_core.exceptions import NotFound, UnprocessableEntity
from ratis_core.models.product import Product
from ratis_core.models.product_contributions import ProductContribution
from services.product_contribute_service import contribute_product_field


@pytest.fixture
def product_no_brand(db) -> Product:
    p = Product(
        ean="3017620422221",
        name="Service Test Empty Brand",
        source="off",
        brands=None,
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def product_with_brand(db) -> Product:
    p = Product(
        ean="3017620422222",
        name="Service Test Filled Brand",
        source="off",
        brands="Existing",
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def product_no_categories(db) -> Product:
    p = Product(
        ean="3017620422223",
        name="Service Test Empty Categories",
        source="off",
        categories_tags=None,
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def trigger_calls():
    return []


@pytest.fixture
def fake_trigger(trigger_calls):
    def _t(user_id, action_type, *args, **kwargs):
        trigger_calls.append(
            {
                "user_id": user_id,
                "action_type": action_type,
                "qualifier": kwargs.get("qualifier"),
                "idempotency_key": kwargs.get("idempotency_key"),
                "context": kwargs.get("context"),
            }
        )

    return _t


@pytest.fixture
def user_id(user):
    return user.id


# ── Apply path ────────────────────────────────────────────────────────────────


def test_apply_brands_updates_product_and_fires_trigger(db, product_no_brand, user_id, fake_trigger, trigger_calls):
    result = contribute_product_field(
        db,
        user_id=user_id,
        ean=product_no_brand.ean,
        field="brands",
        value="Nutella",
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert result["status"] == "applied"
    assert result["applied"] is True
    # Product row was patched.
    p = db.get(Product, product_no_brand.ean)
    assert p.brands == "Nutella"
    # Single trigger_action call.
    assert len(trigger_calls) == 1
    call = trigger_calls[0]
    assert call["action_type"] == "fill_product_field"
    assert call["qualifier"] is None
    assert call["idempotency_key"] == f"contribution:{result['id']}"


def test_apply_categories_writes_value_array(db, product_no_categories, user_id, fake_trigger):
    result = contribute_product_field(
        db,
        user_id=user_id,
        ean=product_no_categories.ean,
        field="categories_tags",
        value=["en:dairies", "en:cheeses"],
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert result["applied"] is True
    row = db.get(ProductContribution, uuid.UUID(result["id"]))
    assert row.value_array == ["en:dairies", "en:cheeses"]
    assert row.value_text is None


def test_field_with_empty_string_is_treated_as_empty(db, user_id, fake_trigger):
    """``brands=''`` (empty string) is treated like NULL → direct apply."""
    p = Product(
        ean="3017620422224",
        name="Empty-string-brand Test",
        source="off",
        brands="",
    )
    db.add(p)
    db.flush()
    db.commit()
    result = contribute_product_field(
        db,
        user_id=user_id,
        ean=p.ean,
        field="brands",
        value="Nutella",
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert result["applied"] is True


def test_array_field_with_empty_list_is_treated_as_empty(db, user_id, fake_trigger):
    """``categories_tags=[]`` is treated like NULL → direct apply."""
    p = Product(
        ean="3017620422225",
        name="Empty-list-cat Test",
        source="off",
        categories_tags=[],
    )
    db.add(p)
    db.flush()
    db.commit()
    result = contribute_product_field(
        db,
        user_id=user_id,
        ean=p.ean,
        field="categories_tags",
        value=["en:dairies"],
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert result["applied"] is True


# ── Queue (pending_review) path ───────────────────────────────────────────────


def test_filled_field_queues_for_review(db, product_with_brand, user_id, fake_trigger, trigger_calls):
    result = contribute_product_field(
        db,
        user_id=user_id,
        ean=product_with_brand.ean,
        field="brands",
        value="Nutella",
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert result["status"] == "pending_review"
    assert result["applied"] is False
    # Product row untouched.
    p = db.get(Product, product_with_brand.ean)
    assert p.brands == "Existing"
    # No trigger call.
    assert trigger_calls == []


# ── Idempotency ──────────────────────────────────────────────────────────────


def test_idempotency_returns_existing_row(db, product_no_brand, user_id, fake_trigger, trigger_calls):
    r1 = contribute_product_field(
        db,
        user_id=user_id,
        ean=product_no_brand.ean,
        field="brands",
        value="Nutella",
        _trigger_action=fake_trigger,
    )
    db.flush()
    r2 = contribute_product_field(
        db,
        user_id=user_id,
        ean=product_no_brand.ean,
        field="brands",
        value="Different",
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert r2["idempotent"] is True
    assert r2["id"] == r1["id"]
    # Only one trigger call.
    assert len(trigger_calls) == 1


# ── Anti-spam daily cap ──────────────────────────────────────────────────────


def test_daily_cap_rejects_when_user_exceeds_limit(db, user_id, fake_trigger, monkeypatch):
    """A user who reaches the per-day contribution cap is rejected with
    ``ContributionDailyCapExceeded`` (HTTP 429 at the route)."""
    from services import product_contribute_service as svc

    # Pin the cap low so the test is fast and deterministic — independent
    # of the production default in ratis_settings.json.
    monkeypatch.setattr(svc, "_load_daily_cap", lambda: 3)

    # Seed `cap` distinct contributions (distinct EAN so the 24h
    # idempotency window never short-circuits an INSERT).
    for i in range(3):
        ean = f"301762049000{i}"
        p = Product(ean=ean, name=f"Cap Test {i}", source="off", brands=None)
        db.add(p)
        db.flush()
        contribute_product_field(
            db,
            user_id=user_id,
            ean=ean,
            field="brands",
            value=f"Brand{i}",
            _trigger_action=fake_trigger,
        )
        db.flush()

    # The (cap+1)-th contribution must be rejected.
    p_over = Product(ean="3017620490099", name="Cap Over", source="off", brands=None)
    db.add(p_over)
    db.flush()
    with pytest.raises(svc.ContributionDailyCapExceeded) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean="3017620490099",
            field="brands",
            value="OverflowBrand",
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_daily_cap_reached"


def test_daily_cap_allows_when_under_limit(db, product_no_brand, user_id, fake_trigger):
    """A user under the cap contributes normally — the cap does not fire
    on the first contribution of the day."""
    result = contribute_product_field(
        db,
        user_id=user_id,
        ean=product_no_brand.ean,
        field="brands",
        value="Nutella",
        _trigger_action=fake_trigger,
    )
    db.flush()
    assert result["status"] == "applied"


# ── Validation ────────────────────────────────────────────────────────────────


def test_unknown_field_raises_422(db, product_no_brand, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_brand.ean,
            field="nutriscore",
            value="A",
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_field_invalid"


def test_array_value_for_scalar_field_raises_422(db, product_no_brand, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_brand.ean,
            field="brands",
            value=["nutella"],
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_type"


def test_scalar_value_for_array_field_raises_422(db, product_no_categories, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_categories.ean,
            field="categories_tags",
            value="en:dairies",
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_type"


def test_too_long_scalar_raises_422(db, product_no_brand, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_brand.ean,
            field="brands",
            value="x" * 250,
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_too_long"


def test_control_chars_raises_422(db, product_no_brand, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_brand.ean,
            field="brands",
            value="Nutella\x05evil",
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_invalid_chars"


def test_invalid_off_tag_shape_raises_422(db, product_no_categories, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_categories.ean,
            field="categories_tags",
            value=["NotATag"],
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_invalid_tag"


def test_array_with_too_many_entries_raises_422(db, product_no_categories, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_categories.ean,
            field="categories_tags",
            value=[f"en:tag-{i}" for i in range(31)],
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_too_many_entries"


def test_empty_array_raises_422(db, product_no_categories, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_categories.ean,
            field="categories_tags",
            value=[],
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_empty"


def test_whitespace_scalar_raises_422(db, product_no_brand, user_id, fake_trigger):
    with pytest.raises(UnprocessableEntity) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean=product_no_brand.ean,
            field="brands",
            value="   ",
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "contribution_value_empty"


# ── 404 ─────────────────────────────────────────────────────────────────────────


def test_unknown_ean_raises_404(db, user_id, fake_trigger, trigger_calls):
    with pytest.raises(NotFound) as exc:
        contribute_product_field(
            db,
            user_id=user_id,
            ean="9999999999999",
            field="brands",
            value="Nutella",
            _trigger_action=fake_trigger,
        )
    assert exc.value.detail == "product_not_found"
    assert trigger_calls == []
