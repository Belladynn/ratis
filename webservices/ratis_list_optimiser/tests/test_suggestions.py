import uuid
from datetime import UTC, date, datetime

from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.shopping import ShoppingList, ShoppingListItem


class TestEligibility:
    """GET /api/v1/suggestions/eligibility"""

    def test_eligible(self, user_client, db, user, store):
        """User with >= 3 receipts is eligible."""
        for i in range(4):
            r = Receipt(user_id=user.id, store_id=store.id, purchased_at=date(2026, 4, i + 1))
            db.add(r)
            db.flush()
            ean = f"300000000000{i}"
            p = Product(ean=ean, name=f"Product {i}", source="off")
            db.add(p)
            db.flush()
            db.add(
                Scan(
                    user_id=user.id,
                    store_id=store.id,
                    product_ean=ean,
                    price=100,
                    scan_type="receipt",
                    status="accepted",
                    receipt_id=r.id,
                    scanned_name=f"Product {i}",
                )
            )
        db.commit()

        resp = user_client.get("/api/v1/suggestions/eligibility")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eligible"] is True
        assert data["receipt_count"] == 4
        assert data["min_required"] == 3

    def test_not_eligible(self, user_client, db, user, store):
        """User with < 3 receipts is not eligible."""
        r = Receipt(user_id=user.id, store_id=store.id, purchased_at=date(2026, 4, 1))
        db.add(r)
        db.flush()
        p = Product(ean="3000000000001", name="Product 1", source="off")
        db.add(p)
        db.flush()
        db.add(
            Scan(
                user_id=user.id,
                store_id=store.id,
                product_ean="3000000000001",
                price=100,
                scan_type="receipt",
                status="accepted",
                receipt_id=r.id,
                scanned_name="Product 1",
            )
        )
        db.commit()

        resp = user_client.get("/api/v1/suggestions/eligibility")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eligible"] is False
        assert data["receipt_count"] == 1

    def test_no_receipts(self, user_client, db, user):
        """User with 0 receipts."""
        resp = user_client.get("/api/v1/suggestions/eligibility")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eligible"] is False
        assert data["receipt_count"] == 0

    def test_auth_required(self, client):
        resp = client.get("/api/v1/suggestions/eligibility")
        assert resp.status_code == 401


class TestGenerate:
    """POST /api/v1/suggestions/generate"""

    def test_generate_suggestions(self, user_client, db, user, store):
        """Frequent items are added to the list."""
        products = []
        for i in range(4):
            p = Product(ean=f"300000000000{i}", name=f"Product {i}", source="off")
            db.add(p)
            products.append(p)
        db.flush()

        # product_0: appears in 4/4 receipts (100%) -> suggest
        # product_1: appears in 3/4 receipts (75%)  -> suggest
        # product_2: appears in 2/4 receipts (50%)  -> suggest
        # product_3: appears in 1/4 receipts (25%)  -> NOT suggest
        scan_seq = 0
        for i in range(4):
            r = Receipt(user_id=user.id, store_id=store.id, purchased_at=date(2026, 4, i + 1))
            db.add(r)
            db.flush()
            scan_seq += 1
            db.add(
                Scan(
                    user_id=user.id,
                    store_id=store.id,
                    product_ean=products[0].ean,
                    price=100,
                    scan_type="receipt",
                    status="accepted",
                    receipt_id=r.id,
                    scanned_name="P0",
                    scanned_at=datetime(2026, 4, i + 1, 10, 0, scan_seq, tzinfo=UTC),
                )
            )
            if i < 3:
                scan_seq += 1
                db.add(
                    Scan(
                        user_id=user.id,
                        store_id=store.id,
                        product_ean=products[1].ean,
                        price=200,
                        scan_type="receipt",
                        status="accepted",
                        receipt_id=r.id,
                        scanned_name="P1",
                        scanned_at=datetime(2026, 4, i + 1, 10, 0, scan_seq, tzinfo=UTC),
                    )
                )
            if i < 2:
                scan_seq += 1
                db.add(
                    Scan(
                        user_id=user.id,
                        store_id=store.id,
                        product_ean=products[2].ean,
                        price=300,
                        scan_type="receipt",
                        status="accepted",
                        receipt_id=r.id,
                        scanned_name="P2",
                        scanned_at=datetime(2026, 4, i + 1, 10, 0, scan_seq, tzinfo=UTC),
                    )
                )
            if i == 0:
                scan_seq += 1
                db.add(
                    Scan(
                        user_id=user.id,
                        store_id=store.id,
                        product_ean=products[3].ean,
                        price=400,
                        scan_type="receipt",
                        status="accepted",
                        receipt_id=r.id,
                        scanned_name="P3",
                        scanned_at=datetime(2026, 4, i + 1, 10, 0, scan_seq, tzinfo=UTC),
                    )
                )

        sl = ShoppingList(user_id=user.id, name="My List", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post("/api/v1/suggestions/generate", json={"list_id": str(sl.id)})
        assert resp.status_code == 200
        data = resp.json()

        suggested_eans = [s["product_ean"] for s in data["suggestions"]]
        assert products[0].ean in suggested_eans
        assert products[1].ean in suggested_eans
        assert products[2].ean in suggested_eans
        assert products[3].ean not in suggested_eans

        assert data["added_to_list"] == 3

    def test_not_eligible(self, user_client, db, user, store):
        """User with < 3 receipts -> 422."""
        sl = ShoppingList(user_id=user.id, name="List", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()

        resp = user_client.post("/api/v1/suggestions/generate", json={"list_id": str(sl.id)})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "not_eligible"

    def test_list_not_found(self, user_client, db, user):
        resp = user_client.post("/api/v1/suggestions/generate", json={"list_id": str(uuid.uuid4())})
        assert resp.status_code == 404

    def test_suggestions_respect_list_cap(self, user_client, db, user, store):
        """LO-24b — suggestions cannot push a list past max_items_per_list (100).

        A user whose list is already full must not overflow it via suggestions.
        """
        # Fill the list to the 100-item cap with filler products.
        sl = ShoppingList(user_id=user.id, name="Full", has_default_name=False)
        db.add(sl)
        db.flush()
        for i in range(100):
            ean = f"9{i:012d}"
            db.add(Product(ean=ean, name=f"Filler {i}", source="off"))
            db.flush()
            db.add(ShoppingListItem(list_id=sl.id, product_ean=ean, quantity=1))
        db.flush()

        # Build a frequent product that suggestions would want to add.
        freq = Product(ean="3000000000777", name="Frequent", source="off")
        db.add(freq)
        db.flush()
        for i in range(3):
            r = Receipt(
                user_id=user.id,
                store_id=store.id,
                purchased_at=date(2026, 4, i + 1),
            )
            db.add(r)
            db.flush()
            db.add(
                Scan(
                    user_id=user.id,
                    store_id=store.id,
                    product_ean=freq.ean,
                    price=100,
                    scan_type="receipt",
                    status="accepted",
                    receipt_id=r.id,
                    scanned_name="Frequent",
                    scanned_at=datetime(2026, 4, i + 1, 10, 0, 0, tzinfo=UTC),
                )
            )
        db.commit()

        resp = user_client.post("/api/v1/suggestions/generate", json={"list_id": str(sl.id)})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # The frequent product was a candidate but the list is full.
        assert data["added_to_list"] == 0
        # List still capped at 100 — no overflow.
        count = db.query(ShoppingListItem).filter_by(list_id=sl.id).count()
        assert count == 100

    def test_skips_already_in_list(self, user_client, db, user, store):
        """Items already in the list are not re-added."""
        p = Product(ean="3000000000001", name="Already There", source="off")
        db.add(p)
        db.flush()

        for i in range(3):
            r = Receipt(user_id=user.id, store_id=store.id, purchased_at=date(2026, 4, i + 1))
            db.add(r)
            db.flush()
            db.add(
                Scan(
                    user_id=user.id,
                    store_id=store.id,
                    product_ean=p.ean,
                    price=100,
                    scan_type="receipt",
                    status="accepted",
                    receipt_id=r.id,
                    scanned_name="P",
                    scanned_at=datetime(2026, 4, i + 1, 10, 0, 0, tzinfo=UTC),
                )
            )

        sl = ShoppingList(user_id=user.id, name="List", has_default_name=False)
        db.add(sl)
        db.flush()
        db.add(ShoppingListItem(list_id=sl.id, product_ean=p.ean, quantity=1))
        db.flush()
        db.commit()

        resp = user_client.post("/api/v1/suggestions/generate", json={"list_id": str(sl.id)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["added_to_list"] == 0


class TestGenerateValidation:
    """suggestions/generate rejects unknown fields (audit LO-09)."""

    def test_generate_rejects_extra_field(self, user_client, db, user):
        sl = ShoppingList(user_id=user.id, name="S", has_default_name=False)
        db.add(sl)
        db.flush()
        db.commit()
        resp = user_client.post(
            "/api/v1/suggestions/generate",
            json={"list_id": str(sl.id), "bogus": 1},
        )
        assert resp.status_code == 422
