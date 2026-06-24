"""Tests for scan-check auto-tick endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC

from ratis_core.models.shopping import ShoppingList, ShoppingListItem


class TestScanCheck:
    """POST /lists/{id}/scan-check — auto-check item by barcode scan."""

    def test_scan_checks_item(self, user_client, db, user, product):
        """Scanning a product that is in the list checks it off."""
        sl = ShoppingList(user_id=user.id, name="Scan test", has_default_name=False)
        db.add(sl)
        db.flush()
        item = ShoppingListItem(list_id=sl.id, product_ean=product.ean, quantity=1)
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/scan-check",
            json={"product_ean": product.ean},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "checked"
        assert body["item"]["product_ean"] == product.ean
        assert body["item"]["checked"] is True
        assert body["item"]["checked_at"] is not None

    def test_scan_already_checked(self, user_client, db, user, product):
        """Scanning a product already checked returns already_checked."""
        sl = ShoppingList(user_id=user.id, name="Already", has_default_name=False)
        db.add(sl)
        db.flush()
        # PG ``checked_at_check`` : checked=true ⇒ checked_at NOT NULL.
        from datetime import datetime as _dt

        item = ShoppingListItem(
            list_id=sl.id,
            product_ean=product.ean,
            quantity=1,
            checked=True,
            checked_at=_dt.now(UTC),
        )
        db.add(item)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/scan-check",
            json={"product_ean": product.ean},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "already_checked"
        assert body["item"]["checked"] is True

    def test_scan_not_in_list(self, user_client, db, user, product):
        """Scanning a product not in the list returns not_in_list with product info."""
        sl = ShoppingList(user_id=user.id, name="Empty", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/scan-check",
            json={"product_ean": product.ean},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_in_list"
        assert body["product"]["ean"] == product.ean
        assert body["product"]["name"] == product.name

    def test_scan_unknown_product(self, user_client, db, user):
        """Scanning a product that doesn't exist in DB returns not_in_list with null product."""
        sl = ShoppingList(user_id=user.id, name="Unknown", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/scan-check",
            json={"product_ean": "0000000000000"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_in_list"
        assert body["product"] is None

    def test_scan_check_not_owner(self, user_client, db, user):
        """Cannot scan-check a list you don't own."""
        from ratis_core.models.user import User

        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            display_name="Other",
            account_type="oauth",
            is_deleted=False,
        )
        db.add(other)
        db.flush()
        sl = ShoppingList(user_id=other.id, name="Not mine", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/scan-check",
            json={"product_ean": "3017620422003"},
        )
        assert resp.status_code == 403

    def test_scan_check_list_not_found(self, user_client, db, user):
        resp = user_client.post(
            f"/api/v1/lists/{uuid.uuid4()}/scan-check",
            json={"product_ean": "3017620422003"},
        )
        assert resp.status_code == 404

    def test_scan_check_auth_required(self, client, db):
        resp = client.post(
            f"/api/v1/lists/{uuid.uuid4()}/scan-check",
            json={"product_ean": "3017620422003"},
        )
        assert resp.status_code == 401


class TestScanCheckValidation:
    """scan-check rejects malformed product_ean (audit LO-08/09)."""

    def test_scan_check_rejects_non_numeric_ean(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="V", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.post(f"/api/v1/lists/{sl.id}/scan-check", json={"product_ean": "xyz"})
        assert resp.status_code == 422

    def test_scan_check_rejects_extra_field(self, user_client, db, user, product):
        sl = ShoppingList(user_id=user.id, name="V", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.post(
            f"/api/v1/lists/{sl.id}/scan-check",
            json={"product_ean": product.ean, "bogus": True},
        )
        assert resp.status_code == 422
