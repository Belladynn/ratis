"""Tests for shopping list CRUD — lists and items."""

from __future__ import annotations

import uuid

from ratis_core.models.product import Product
from ratis_core.models.shopping import ShoppingList, ShoppingListItem


class TestShoppingLists:
    """Shopping list CRUD tests."""

    def test_create_list_with_name(self, user_client, db, user):
        resp = user_client.post("/api/v1/lists", json={"name": "Courses samedi"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Courses samedi"
        assert body["has_default_name"] is False
        assert body["items"] == []

    def test_create_default_list(self, user_client, db, user):
        resp = user_client.post("/api/v1/lists", json={})
        assert resp.status_code == 201
        body = resp.json()
        assert body["has_default_name"] is True
        # Never send empty string as name
        assert body["name"] is None

    def test_get_lists(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Ma liste", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(
            list_id=sl.id,
            product_ean=product.ean,
            quantity=2,
            checked=False,
        )
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.get("/api/v1/lists")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1
        found = [x for x in body if x["id"] == str(sl.id)]
        assert len(found) == 1
        assert found[0]["item_count"] == 1
        assert found[0]["unchecked_count"] == 1
        assert found[0]["name"] == "Ma liste"

    def test_get_lists_empty(self, user_client, db, user):
        resp = user_client.get("/api/v1/lists")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_list_detail(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Detail", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(
            list_id=sl.id,
            product_ean=product.ean,
            quantity=1,
        )
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.get(f"/api/v1/lists/{sl.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(sl.id)
        assert body["name"] == "Detail"
        assert len(body["items"]) == 1
        assert body["items"][0]["product_ean"] == product.ean
        assert body["items"][0]["product_name"] == product.name

    def test_get_list_not_found(self, user_client, db, user):
        resp = user_client.get(f"/api/v1/lists/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "list_not_found"

    def test_get_list_not_owner(self, user_client, client, db, user):
        other = _create_other_user(db)
        sl = ShoppingList(user_id=other.id, name="Private", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.get(f"/api/v1/lists/{sl.id}")
        assert resp.status_code == 403

    def test_update_list_name(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="Old", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.patch(f"/api/v1/lists/{sl.id}", json={"name": "New"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "New"
        assert body["has_default_name"] is False

    def test_delete_list(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="Delete me", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.delete(f"/api/v1/lists/{sl.id}")
        assert resp.status_code == 204

        # Verify it's gone
        resp2 = user_client.get(f"/api/v1/lists/{sl.id}")
        assert resp2.status_code == 404

    def test_delete_list_not_owner(self, user_client, client, db, user):
        other = _create_other_user(db)
        sl = ShoppingList(user_id=other.id, name="Not mine", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.delete(f"/api/v1/lists/{sl.id}")
        assert resp.status_code == 403


class TestShoppingListItems:
    """Shopping list item CRUD tests."""

    def test_add_item(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Items", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["product_ean"] == product.ean
        assert body["product_name"] == product.name
        assert body["quantity"] == 1.0
        assert body["checked"] is False
        # Wave 12 — every item response now carries a derived category
        # (snake-case key) so the FE can group rows under section
        # headers. The fixture ``product`` has no storage_type and no
        # categories_tags → mapper returns « autres ».
        assert body["category"] == "autres"

    def test_get_list_detail_items_carry_category_field(self, user_client, db, user):
        """Wave 12 — ``GET /lists/{id}`` items expose ``category`` derived
        from ``products.storage_type`` + ``categories_tags``. End-to-end
        check that the resolver runs against persisted product rows and
        the route serialises the value untouched."""
        # Seed three products spanning three category buckets so the
        # test covers more than just the « autres » default branch.
        bakery = Product(
            ean="3017620499001",
            name="Baguette tradition",
            categories_tags=["en:breads"],
            source="off",
        )
        beverage = Product(
            ean="3017620499002",
            name="Eau Evian 1.5L",
            categories_tags=["en:waters", "en:beverages"],
            source="off",
        )
        dairy = Product(
            ean="3017620499003",
            name="Yaourt nature",
            categories_tags=["en:dairies", "en:yogurts"],
            storage_type="fresh",
            source="off",
        )
        for p in (bakery, beverage, dairy):
            db.add(p)
        db.flush()
        sl = ShoppingList(user_id=user.id, name="Cat", has_default_name=False)
        db.add(sl)
        db.flush()
        for p in (bakery, beverage, dairy):
            db.add(ShoppingListItem(list_id=sl.id, product_ean=p.ean, quantity=1))
        db.flush()
        db.commit()

        resp = user_client.get(f"/api/v1/lists/{sl.id}")
        assert resp.status_code == 200
        by_ean = {it["product_ean"]: it for it in resp.json()["items"]}
        assert by_ean["3017620499001"]["category"] == "boulangerie"
        assert by_ean["3017620499002"]["category"] == "boissons"
        assert by_ean["3017620499003"]["category"] == "frais"

    def test_add_item_with_quantity(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Qty", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean, "quantity": 3},
        )
        assert resp.status_code == 201
        assert resp.json()["quantity"] == 3.0

    def test_add_item_product_not_found(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="No product", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": "0000000000000"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "product_not_found"

    def test_add_item_already_in_list(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Dup", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "item_already_in_list"

    def test_add_item_list_not_owner(self, user_client, client, db, user, product):
        other = _create_other_user(db)
        sl = ShoppingList(user_id=other.id, name="Other", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean},
        )
        assert resp.status_code == 403

    def test_update_item_quantity(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Update qty", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.patch(
            f"/api/v1/lists/{sl.id}/items/{item.id}",
            json={"quantity": 5},
        )
        assert resp.status_code == 200
        assert resp.json()["quantity"] == 5.0

    def test_add_item_quantity_over_cap_rejected(self, user_client, db, user, product):
        """LO-24a — adding an item with quantity > 30 returns 422."""
        sl = ShoppingList(user_id=user.id, name="Over", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean, "quantity": 31},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "quantity_too_high"

    def test_add_item_quantity_at_cap_accepted(self, user_client, db, user, product):
        """LO-24a — quantity exactly at the cap (30) is accepted."""
        sl = ShoppingList(user_id=user.id, name="AtCap", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean, "quantity": 30},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["quantity"] == 30.0

    def test_update_item_quantity_over_cap_rejected(self, user_client, db, user, product):
        """LO-24a — updating an item to quantity > 30 returns 422."""
        sl = ShoppingList(user_id=user.id, name="UpdOver", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.patch(
            f"/api/v1/lists/{sl.id}/items/{item.id}",
            json={"quantity": 99},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "quantity_too_high"

    def test_update_item_checked(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Check", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.patch(
            f"/api/v1/lists/{sl.id}/items/{item.id}",
            json={"checked": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["checked"] is True
        assert body["checked_at"] is not None

    def test_delete_item(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Del item", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.delete(f"/api/v1/lists/{sl.id}/items/{item.id}")
        assert resp.status_code == 204

    def test_auth_required(self, client, db):
        """Any endpoint without JWT returns 401."""
        resp = client.get("/api/v1/lists")
        assert resp.status_code == 401


class TestTemplates:
    """Template list tests."""

    def test_save_as_template(self, user_client, db, user, product):
        """Save a list as template — creates a copy with is_template=true."""
        sl = ShoppingList(user_id=user.id, name="Weekly", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=2)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/{sl.id}/save-as-template")
        assert resp.status_code == 201
        body = resp.json()
        assert body["is_template"] is True
        assert body["name"] == "Weekly"
        assert len(body["items"]) == 1
        assert body["items"][0]["product_ean"] == product.ean
        assert body["items"][0]["quantity"] == 2.0
        # Original list still exists and is not a template
        orig = user_client.get(f"/api/v1/lists/{sl.id}")
        assert orig.json()["is_template"] is False

    def test_save_as_template_max_reached(self, user_client, db, user, product):
        """Cannot exceed max_templates_per_user (3)."""
        for i in range(3):
            t = ShoppingList(user_id=user.id, name=f"Template {i}", has_default_name=False, is_template=True)
            db.add(t)
        db.flush()
        db.commit()

        sl = ShoppingList(user_id=user.id, name="New list", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/{sl.id}/save-as-template")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "max_templates_reached"

    def test_create_from_template(self, user_client, db, user, product):
        """Create a new list from a template — copies items."""
        tpl = ShoppingList(user_id=user.id, name="Base courses", has_default_name=False, is_template=True)
        db.add(tpl)
        db.flush()
        item = ShoppingListItem(list_id=tpl.id, product_ean=product.ean, quantity=3)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/from-template/{tpl.id}",
            json={"name": "Courses lundi"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["is_template"] is False
        assert body["name"] == "Courses lundi"
        assert len(body["items"]) == 1
        assert body["items"][0]["product_ean"] == product.ean
        assert body["items"][0]["quantity"] == 3.0

    def test_create_from_template_no_name(self, user_client, db, user, product):
        """Create from template without name — gets default name."""
        tpl = ShoppingList(user_id=user.id, name="Base", has_default_name=False, is_template=True)
        db.add(tpl)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/from-template/{tpl.id}", json={})
        assert resp.status_code == 201
        assert resp.json()["has_default_name"] is True

    def test_create_from_non_template(self, user_client, db, user):
        """Cannot create from a non-template list."""
        sl = ShoppingList(user_id=user.id, name="Regular", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/from-template/{sl.id}", json={})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "not_a_template"

    def test_get_lists_shows_is_template(self, user_client, db, user):
        """GET /lists includes is_template field."""
        sl = ShoppingList(user_id=user.id, name="Regular", has_default_name=False)
        tpl = ShoppingList(user_id=user.id, name="Template", has_default_name=False, is_template=True)
        db.add_all([sl, tpl])
        db.flush()
        db.commit()

        resp = user_client.get("/api/v1/lists")
        assert resp.status_code == 200
        body = resp.json()
        templates = [x for x in body if x["is_template"] is True]
        regulars = [x for x in body if x["is_template"] is False]
        assert len(templates) == 1
        assert len(regulars) == 1

    def test_optimize_template_rejected(self, user_client, db, user, product):
        """Cannot optimize a template list."""
        tpl = ShoppingList(user_id=user.id, name="Template", has_default_name=False, is_template=True)
        db.add(tpl)
        db.flush()
        item = ShoppingListItem(list_id=tpl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/{tpl.id}/optimize", json={"lat": 48.857, "lng": 2.352})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "cannot_optimize_template"


class TestClearList:
    """Clear all items from a list."""

    def test_clear_list(self, user_client, db, user, product):
        """Clear removes all items from the list."""
        sl = ShoppingList(user_id=user.id, name="Clear me", has_default_name=False)
        db.add(sl)
        db.flush()
        for ean in [product.ean]:
            item = ShoppingListItem(list_id=sl.id, product_ean=ean, quantity=1)
            db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/{sl.id}/clear")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []

        # Verify list still exists
        detail = user_client.get(f"/api/v1/lists/{sl.id}")
        assert detail.status_code == 200
        assert len(detail.json()["items"]) == 0

    def test_clear_list_not_found(self, user_client, db, user):
        resp = user_client.post(f"/api/v1/lists/{uuid.uuid4()}/clear")
        assert resp.status_code == 404

    def test_clear_list_not_owner(self, user_client, db, user):
        other = _create_other_user(db)
        sl = ShoppingList(user_id=other.id, name="Not mine", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/{sl.id}/clear")
        assert resp.status_code == 403


# ── Helpers ──────────────────────────────────────────────────────────


def _create_other_user(db):
    """Create a second user for ownership tests."""
    from ratis_core.models.user import User

    _uid = uuid.uuid4()
    u = User(
        id=_uid,
        email="other@ratis.fr",
        display_name="OtherUser",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    return u


# ===========================================================================
# Audit fixes — EAN bounds, name validation, list updated_at, template guard
# ===========================================================================


class TestRequestValidation:
    """Request payloads reject malformed input (audit LO-08/09/10)."""

    def test_add_item_rejects_non_numeric_ean(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="L", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.post(f"/api/v1/lists/{sl.id}/items", json={"product_ean": "abc"})
        assert resp.status_code == 422

    def test_add_item_rejects_overlong_ean(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="L", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.post(f"/api/v1/lists/{sl.id}/items", json={"product_ean": "1" * 30})
        assert resp.status_code == 422

    def test_add_item_rejects_extra_field(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="L", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/items",
            json={"product_ean": product.ean, "bogus": 1},
        )
        assert resp.status_code == 422

    def test_update_list_rejects_empty_name(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="Old", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.patch(f"/api/v1/lists/{sl.id}", json={"name": ""})
        assert resp.status_code == 422

    def test_update_list_rejects_whitespace_name(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="Old", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.patch(f"/api/v1/lists/{sl.id}", json={"name": "   "})
        assert resp.status_code == 422

    def test_create_list_rejects_empty_name(self, user_client):
        resp = user_client.post("/api/v1/lists", json={"name": ""})
        assert resp.status_code == 422


class TestListUpdatedAtBump:
    """Content mutations refresh the parent list's updated_at (audit LO-05)."""

    def test_add_item_bumps_updated_at(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Bump", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        before = sl.updated_at

        resp = user_client.post(f"/api/v1/lists/{sl.id}/items", json={"product_ean": product.ean})
        assert resp.status_code == 201
        db.refresh(sl)
        assert sl.updated_at > before

    def test_clear_list_bumps_updated_at(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="Bump2", has_default_name=False)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1))
        db.flush()
        db.commit()
        before = sl.updated_at

        resp = user_client.post(f"/api/v1/lists/{sl.id}/clear")
        assert resp.status_code == 200
        db.refresh(sl)
        assert sl.updated_at > before


class TestSaveAsTemplateGuard:
    """save-as-template rejects a list that is already a template (audit LO-11)."""

    def test_save_template_from_template_rejected(self, user_client, db, user, product):
        tpl = ShoppingList(user_id=user.id, name="Already", has_default_name=False, is_template=True)
        db.add(tpl)
        db.flush()
        db.commit()

        resp = user_client.post(f"/api/v1/lists/{tpl.id}/save-as-template")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "already_a_template"
