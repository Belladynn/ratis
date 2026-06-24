from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from ratis_core.models.analytics import UserPreferences
from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan

from tests.conftest import make_token


def _auth(user):
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _seed_receipt(db, *, user, store) -> Receipt:
    """Seed a sibling Receipt so a ``scan_type='receipt'`` row satisfies
    CHECK ``receipt_required`` (receipt scans MUST have receipt_id NOT
    NULL — Bug 6 ORM mirror)."""
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        purchased_at=date.today(),
    )
    db.add(r)
    db.flush()
    return r


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def product_nutella(db) -> Product:
    p = Product(
        ean="3017620422003",
        name="Nutella 400g",
        source="off",
        brands="Ferrero",
        product_quantity=400,
        product_quantity_unit="g",
        storage_type="ambient",
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def unmatched_scan(db, user, store) -> Scan:
    """Receipt scan with no product match."""
    r = _seed_receipt(db, user=user, store=store)
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        receipt_id=r.id,
        product_ean=None,
        scanned_name="NUT 400g",
        scan_type="receipt",
        status="unmatched",
        price=Decimal("2.49"),
        quantity=Decimal("1"),
        image_url=None,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


@pytest.fixture
def user_prefs(db, user) -> UserPreferences:
    p = UserPreferences(user_id=user.id, search_radius_km=5)
    db.add(p)
    db.flush()
    db.commit()
    return p


# ── resolve unmatched ─────────────────────────────────────────────────────────


class TestResolveUnmatched:
    def test_resolves_unmatched_scan(self, client, user, product_nutella, unmatched_scan, user_prefs):
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(unmatched_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["product"]["ean"] == product_nutella.ean
        assert body["resolved_scan"]["scan_id"] == str(unmatched_scan.id)
        assert body["resolved_scan"]["product_ean"] == product_nutella.ean
        assert body["resolved_scan"]["scanned_name"] == "NUT 400g"

    def test_scan_updated_in_db(self, client, user, product_nutella, unmatched_scan, user_prefs, db):
        client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(unmatched_scan.id)},
            headers=_auth(user),
        )
        db.expire_all()
        updated = db.get(Scan, unmatched_scan.id)
        assert updated.product_ean == product_nutella.ean
        assert updated.status == "accepted"

    def test_user_verified_at_set(self, client, user, product_nutella, unmatched_scan, user_prefs, db):
        client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(unmatched_scan.id)},
            headers=_auth(user),
        )
        db.expire_all()
        assert db.get(Scan, unmatched_scan.id).user_verified_at is not None

    def test_response_includes_verification_flags(self, client, user, product_nutella, unmatched_scan, user_prefs):
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(unmatched_scan.id)},
            headers=_auth(user),
        )
        resolved = resp.json()["resolved_scan"]
        assert resolved["user_verified"] is True
        assert "globally_verified" in resolved

    def test_scan_not_found_returns_404(self, client, user, product_nutella, user_prefs):
        resp = client.post(
            "/api/v1/scan/barcode", json={"ean": product_nutella.ean, "scan_id": str(uuid.uuid4())}, headers=_auth(user)
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "scan_not_found"

    def test_scan_belonging_to_other_user_returns_403(self, client, user, product_nutella, store, user_prefs, db):
        from ratis_core.models.user import User

        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            account_type="oauth",
            is_deleted=False,
        )
        db.add(other)
        db.flush()
        r = _seed_receipt(db, user=other, store=store)
        other_scan = Scan(
            id=uuid.uuid4(),
            user_id=other.id,
            store_id=store.id,
            receipt_id=r.id,
            product_ean=None,
            scanned_name="NUT 400g",
            scan_type="receipt",
            status="unmatched",
            price=Decimal("2.49"),
            quantity=Decimal("1"),
            image_url=None,
        )
        db.add(other_scan)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(other_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_unknown_product_returns_404(self, client, user, unmatched_scan, user_prefs):
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": "9999999999999", "scan_id": str(unmatched_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "product_not_found"

    def test_already_accepted_scan_allows_user_override(self, client, user, product_nutella, store, user_prefs, db):
        """User can OVERRIDE an auto-matched scan with a barcode scan (the
        physical barcode has higher priority than fuzzy similarity).
        Use case: the matcher chose the wrong product because OFF data is
        crappy — user corrects it by scanning the actual barcode."""
        r = _seed_receipt(db, user=user, store=store)
        accepted_scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            receipt_id=r.id,
            product_ean=product_nutella.ean,
            scanned_name="Nutella 400g",
            scan_type="receipt",
            status="accepted",
            price=Decimal("2.50"),
            quantity=Decimal("1"),
            image_url=None,
        )
        db.add(accepted_scan)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(accepted_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 200, resp.text

    def test_pending_scan_returns_409(self, client, user, product_nutella, store, user_prefs, db):
        """Scan still being processed by the pipeline → race condition risk, refuse."""
        r = _seed_receipt(db, user=user, store=store)
        pending_scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            receipt_id=r.id,
            product_ean=None,
            scanned_name="Nutella 400g",
            scan_type="receipt",
            status="pending",
            price=Decimal("2.50"),
            quantity=Decimal("1"),
            image_url=None,
        )
        db.add(pending_scan)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(pending_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "scan_already_resolved"

    def test_rejected_scan_returns_409(self, client, user, product_nutella, store, user_prefs, db):
        """Rejected = terminal (ticket data unusable) → no override possible."""
        r = _seed_receipt(db, user=user, store=store)
        rejected_scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            receipt_id=r.id,
            product_ean=None,
            scanned_name="garbage",
            scan_type="receipt",
            status="rejected",
            rejected_reason="parsing_garbage",
            price=Decimal("2.50"),
            quantity=Decimal("1"),
            image_url=None,
        )
        db.add(rejected_scan)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(rejected_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "scan_already_resolved"

    def test_unresolved_scan_v3_resolvable_via_barcode(self, client, user, product_nutella, store, user_prefs, db):
        """v3 'unresolved' is the equivalent of v2 'unmatched' — user can resolve."""
        r = _seed_receipt(db, user=user, store=store)
        unresolved_scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            receipt_id=r.id,
            product_ean=None,
            scanned_name="OCR garbage label",
            scan_type="receipt",
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
            price=Decimal("2.50"),
            quantity=Decimal("1"),
            image_url=None,
        )
        db.add(unresolved_scan)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(unresolved_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 200, resp.text

    def test_electronic_label_scan_returns_409(self, client, user, product_nutella, store, user_prefs, db):
        """Electronic label scans have their own pipeline — not resolvable via barcode."""
        label_scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            product_ean=None,
            scanned_name="Nutella 400g",
            scan_type="electronic_label",
            status="unmatched",
            price=Decimal("2.49"),
            quantity=Decimal("1"),
            image_url=None,
        )
        db.add(label_scan)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": product_nutella.ean, "scan_id": str(label_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "scan_already_resolved"

    def test_missing_scan_id_returns_422(self, client, user, product_nutella):
        resp = client.post("/api/v1/scan/barcode", json={"ean": product_nutella.ean}, headers=_auth(user))
        assert resp.status_code == 422

    def test_no_token_returns_401(self, client, product_nutella, unmatched_scan):
        resp = client.post("/api/v1/scan/barcode", json={"ean": product_nutella.ean, "scan_id": str(unmatched_scan.id)})
        assert resp.status_code == 401


# ── EAN validation ─────────────────────────────────────────────────────────────


class TestBarcodeScanValidation:
    def test_ean_too_short_returns_422(self, client, user, unmatched_scan):
        resp = client.post(
            "/api/v1/scan/barcode", json={"ean": "123", "scan_id": str(unmatched_scan.id)}, headers=_auth(user)
        )
        assert resp.status_code == 422

    def test_ean_non_numeric_returns_422(self, client, user, unmatched_scan):
        resp = client.post(
            "/api/v1/scan/barcode", json={"ean": "ABCD1234567", "scan_id": str(unmatched_scan.id)}, headers=_auth(user)
        )
        assert resp.status_code == 422

    def test_ean_too_long_returns_422(self, client, user, unmatched_scan):
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": "12345678901234", "scan_id": str(unmatched_scan.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 422


# ── coherence check / fuzzy-pre-refonte paths ────────────────────────────────
#
# Removed 2026-05-03 : the legacy 409 ``product_mismatch`` path and the
# ``fuzzy_confirmed`` / ``manual`` match_method values were dropped in the
# pipeline consensus-only refonte (2026-05-02). The barcode service
# now always emits ``match_method='barcode'`` — the user's physical scan
# IS the verification ; no fuzzy comparison is performed. Coverage of
# the post-refonte contract lives in :class:`TestConsensusOnlyContract`
# below. See DECISIONS_PENDING § DP-pipeline-v3-consensus-only-refonte.


class TestConsensusOnlyContract:
    """Post-refonte contract (2026-05-02) : every barcode-resolved scan
    receives ``match_method='barcode'``. The user physically scanning IS
    the verification ; no fuzzy/coherence comparison is performed.
    """

    def test_match_method_is_always_barcode(
        self,
        client,
        user,
        product_nutella,
        unmatched_scan,
        db,
    ):
        resp = client.post(
            "/api/v1/scan/barcode",
            json={
                "ean": product_nutella.ean,
                "scan_id": str(unmatched_scan.id),
            },
            headers=_auth(user),
        )
        assert resp.status_code == 200
        body = resp.json()["resolved_scan"]
        assert body["match_method"] == "barcode"
        assert body["product_ean"] == product_nutella.ean
        assert body["user_verified"] is True
        db.expire_all()
        assert db.get(Scan, unmatched_scan.id).match_method == "barcode"


# ── Rate limiting ──────────────────────────────────────────────────────────────


class TestBarcodeRateLimit:
    """POST /scan/barcode is rate-limited to 10/min/user (slowapi)."""

    def test_eleventh_request_returns_429(self, client, user):
        ean = "3017620422003"
        for _ in range(10):
            resp = client.post(
                "/api/v1/scan/barcode",
                json={"ean": ean, "scan_id": str(uuid.uuid4())},
                headers=_auth(user),
            )
            # Non-existent scan → 404, but the request still counts.
            assert resp.status_code == 404
        resp = client.post(
            "/api/v1/scan/barcode",
            json={"ean": ean, "scan_id": str(uuid.uuid4())},
            headers=_auth(user),
        )
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limit_exceeded"
