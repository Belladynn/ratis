"""Tests for POST /api/v1/scan/receipt/{id}/confirm-store and the
``store_candidate_info`` extension on GET /api/v1/scan/receipt/{id}.

PR-B Phase 1 — see ARCH_store_validation.md.
"""

from __future__ import annotations

import uuid
from datetime import date

from ratis_core.models.scan import Receipt
from ratis_core.models.store import Store, StoreValidationHistory
from ratis_core.models.store_candidate import StoreCandidate
from ratis_core.models.user import User

from tests.conftest import make_token


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _make_receipt(db, *, user_id, store_id=None, store_status="unknown") -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=store_id,
        purchased_at=date.today(),
        store_status=store_status,
        image_r2_key=f"{uuid.uuid4()}.jpg",
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _make_candidate(
    db,
    *,
    receipt_id,
    retailer_guess="LIDL",
    address_guess="12 RUE DE LA PAIX",
    postal_code="75002",
    phone="0142345678",
    raw_header="LIDL\n12 RUE DE LA PAIX\n75002 PARIS",
) -> StoreCandidate:
    c = StoreCandidate(
        id=uuid.uuid4(),
        raw_header=raw_header,
        retailer_guess=retailer_guess,
        address_guess=address_guess,
        postal_code=postal_code,
        phone=phone,
        receipt_id=receipt_id,
        status="pending",
    )
    db.add(c)
    db.flush()
    db.commit()
    return c


# ============================================================
# POST /api/v1/scan/receipt/{id}/confirm-store
# ============================================================


class TestConfirmStoreNominal:
    def test_creates_user_suggested_store_pending(self, client, user, db, monkeypatch):
        # process_pending_items is exercised by its own dedicated tests; here we
        # only need to verify the endpoint calls it.
        called: list[uuid.UUID] = []

        def fake_process(db_arg, receipt_arg):
            called.append(receipt_arg.id)
            return []

        monkeypatch.setattr(
            "services.store_confirmation_service.process_pending_items",
            fake_process,
        )

        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        _make_candidate(db, receipt_id=receipt.id)

        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["store_status"] == "pending"
        assert body["validation_status"] == "pending"
        assert body["message"] == "store_pending_validation"
        store_id = uuid.UUID(body["store_id"])

        # Store row created with the right shape.
        s = db.get(Store, store_id)
        assert s is not None
        assert s.source == "user_suggested"
        assert s.validation_status == "pending"
        assert s.suggested_by_user_id == user.id
        assert s.retailer == "lidl"
        assert s.postal_code == "75002"
        assert s.address == "12 RUE DE LA PAIX"
        assert s.phone == "0142345678"
        # lat/lng are placeholders — admin/V2 batch geocodes later.
        assert float(s.lat) == 0.0
        assert float(s.lng) == 0.0

        # Receipt linked + state flipped.
        db.refresh(receipt)
        assert receipt.store_id == store_id
        assert receipt.store_status == "pending"

        # Audit row inserted.
        from sqlalchemy import select

        history = db.scalars(select(StoreValidationHistory).where(StoreValidationHistory.store_id == store_id)).all()
        assert len(history) == 1
        assert history[0].from_status is None
        assert history[0].to_status == "pending"
        assert history[0].reason == "user_confirmed"
        assert history[0].triggered_by == f"user:{user.id}"

        # process_pending_items was invoked.
        assert called == [receipt.id]


class TestConfirmStoreErrors:
    def test_no_token_returns_401(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id)
        _make_candidate(db, receipt_id=receipt.id)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/confirm-store")
        assert resp.status_code == 401

    def test_other_user_returns_403(self, client, user, db):
        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            account_type="oauth",
            is_deleted=False,
        )
        db.add(other)
        db.flush()
        db.commit()
        receipt = _make_receipt(db, user_id=other.id)
        _make_candidate(db, receipt_id=receipt.id)
        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_receipt_not_found_returns_404(self, client, user, db):
        resp = client.post(
            f"/api/v1/scan/receipt/{uuid.uuid4()}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "receipt_not_found"

    def test_already_resolved_returns_409(self, client, user, store, db):
        receipt = _make_receipt(db, user_id=user.id, store_id=store.id, store_status="confirmed")
        _make_candidate(db, receipt_id=receipt.id)
        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "receipt_already_resolved"

    def test_pending_status_returns_409(self, client, user, store, db):
        receipt = _make_receipt(db, user_id=user.id, store_id=store.id, store_status="pending")
        _make_candidate(db, receipt_id=receipt.id)
        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "receipt_already_resolved"

    def test_no_candidate_returns_422(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        # No candidate at all.
        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "candidate_not_found"

    def test_insufficient_data_no_retailer_returns_422(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        _make_candidate(
            db,
            receipt_id=receipt.id,
            retailer_guess=None,
            address_guess="12 RUE",
            postal_code="75002",
        )
        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "insufficient_ocr_data"

    def test_insufficient_data_no_address_or_postal_returns_422(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        _make_candidate(
            db,
            receipt_id=receipt.id,
            retailer_guess="LIDL",
            address_guess=None,
            postal_code=None,
        )
        resp = client.post(
            f"/api/v1/scan/receipt/{receipt.id}/confirm-store",
            headers=_auth(user),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "insufficient_ocr_data"


# ============================================================
# GET /api/v1/scan/receipt/{id} — store_candidate_info extension
# ============================================================


class TestGetReceiptStoreCandidateInfo:
    def test_unknown_with_full_candidate_includes_info(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        _make_candidate(
            db,
            receipt_id=receipt.id,
            retailer_guess="LIDL",
            address_guess="12 RUE DE LA PAIX",
            postal_code="75002",
            phone="0142345678",
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("store_candidate_info") is not None
        info = body["store_candidate_info"]
        assert info["brand_guess"] == "LIDL"
        assert info["address"] == "12 RUE DE LA PAIX"
        assert info["postal_code"] == "75002"
        assert info["phone"] == "0142345678"

    def test_confirmed_does_not_include_info(self, client, user, store, db):
        receipt = _make_receipt(db, user_id=user.id, store_id=store.id, store_status="confirmed")
        # Even if a candidate row exists, we don't expose it on a confirmed receipt.
        _make_candidate(db, receipt_id=receipt.id)
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body.get("store_candidate_info") is None

    def test_unknown_without_candidate_no_info_field(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        # No candidate at all.
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body.get("store_candidate_info") is None

    def test_unknown_with_insufficient_candidate_no_info_field(self, client, user, db):
        receipt = _make_receipt(db, user_id=user.id, store_status="unknown")
        _make_candidate(
            db,
            receipt_id=receipt.id,
            retailer_guess=None,
            address_guess=None,
            postal_code=None,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body.get("store_candidate_info") is None

    def test_pending_with_full_candidate_includes_info(self, client, user, store, db):
        receipt = _make_receipt(db, user_id=user.id, store_id=store.id, store_status="pending")
        _make_candidate(db, receipt_id=receipt.id)
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        # Pending receipts also surface candidate info so the user can see
        # what's blocking the cashback.
        assert body.get("store_candidate_info") is not None
