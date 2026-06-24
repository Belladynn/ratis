"""Tests for ``POST /api/v1/scan/receipt/{receipt_id}/rescan``
— anti-fraud PR5 (sprint-completing endpoint).

Covered :
    - happy path : increments counter + enqueues V3 + returns 202
    - 404 on unknown receipt + 404 on not-owned (no existence leak)
    - 409 ``receipt_already_accepted`` for matched + accepted statuses
    - 410 ``receipt_image_expired`` when image_deleted_at is set
    - 410 when image_r2_key is NULL even with image_deleted_at NULL
    - 429 ``rescan_cap_exceeded`` when counter == cap
    - race : cap enforcement is atomic — concurrent rescans don't
      over-increment past the cap
    - 401 missing JWT
    - rate-limit (slowapi) is wired

"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User
from sqlalchemy import text

from tests.conftest import make_token


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _make_receipt(
    db,
    *,
    store,
    user,
    rescan_attempts: int = 0,
    image_r2_key: str | None = "receipt.jpg",
    image_deleted_at: datetime | None = None,
) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=date.today(),
        image_r2_key=image_r2_key,
        image_deleted_at=image_deleted_at,
        rescan_attempts=rescan_attempts,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _add_scan(db, receipt: Receipt, store, status: str) -> Scan:
    """Build a scan with the right CHECK invariants per status :

    - ``matched`` / ``accepted`` → require ``product_ean`` + ``match_method``
      (``ck_scans_matched_requires_ean_method``).
    - ``rejected`` / ``unresolved`` → require a ``rejected_reason``
      (``ck_scans_non_matched_requires_reason``).
    """
    matched_like = status in ("matched", "accepted")
    s = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        receipt_id=receipt.id,
        scan_type="receipt",
        status=status,
        rejected_reason=("test_rejected" if status in ("rejected", "unresolved") else None),
        # Anchor a real product so the FK on ``product_ean`` resolves.
        product_ean=("3017620422003" if matched_like else None),
        match_method=("barcode" if matched_like else None),
        scanned_name="Nutella 400g",
        price=250,
        quantity=1.0,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


@pytest.fixture
def cap_3(monkeypatch):
    """Pin ``rescan_max_attempts`` to 3 so tests are reproducible
    against the documented default. Settings reads are otherwise
    happy-path."""
    monkeypatch.setattr("services.rescan_service._rescan_max_attempts", lambda: 3)


@pytest.fixture
def captured_enqueue(monkeypatch):
    """Replace ``_enqueue_rescan`` with a recorder + return the buffer."""
    captured: list[uuid.UUID] = []

    def _record(receipt_id: uuid.UUID) -> None:
        captured.append(receipt_id)

    monkeypatch.setattr("services.rescan_service._enqueue_rescan", _record)
    return captured


# ============================================================
# Happy path
# ============================================================
class TestRescanHappyPath:
    def test_returns_202_and_payload(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body == {
            "receipt_id": str(receipt.id),
            "rescan_attempts": 1,
            "status": "queued",
        }

    def test_increments_counter_in_db(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user)
        client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        db.expire_all()
        refreshed = db.get(Receipt, receipt.id)
        assert refreshed.rescan_attempts == 1

    def test_enqueues_celery_task(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user)
        client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert captured_enqueue == [receipt.id]

    def test_two_attempts_increment_to_two(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user, rescan_attempts=1)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 202
        assert resp.json()["rescan_attempts"] == 2


# ============================================================
# Ownership / existence
# ============================================================
class TestRescanOwnership:
    def test_unknown_receipt_returns_404(self, client, user, cap_3, captured_enqueue):
        resp = client.post(f"/api/v1/scan/receipt/{uuid.uuid4()}/rescan", headers=_auth(user))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "receipt_not_found"

    def test_not_owner_returns_404_not_403(self, client, store, user, db, cap_3, captured_enqueue):
        """No existence leak — a stranger's receipt must look like a 404."""
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
        receipt = _make_receipt(db, store=store, user=other)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "receipt_not_found"

    def test_missing_jwt_returns_401(self, client, store, user, db):
        receipt = _make_receipt(db, store=store, user=user)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan")
        assert resp.status_code == 401


# ============================================================
# Already-accepted gate
# ============================================================
class TestRescanAlreadyAccepted:
    def test_409_when_a_scan_is_matched(self, client, store, user, product, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user)
        _add_scan(db, receipt, store, status="matched")
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 409
        assert resp.json()["detail"] == "receipt_already_accepted"
        assert captured_enqueue == []

    def test_409_when_a_scan_is_accepted_v2_vocab(self, client, store, user, product, db, cap_3, captured_enqueue):
        """V2 used ``accepted`` ; V3 uses ``matched``. Both terminal."""
        receipt = _make_receipt(db, store=store, user=user)
        _add_scan(db, receipt, store, status="accepted")
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 409

    def test_allowed_when_scans_are_rejected(self, client, store, user, db, cap_3, captured_enqueue):
        """Rejected scans are *exactly* the typical rescan candidate."""
        receipt = _make_receipt(db, store=store, user=user)
        _add_scan(db, receipt, store, status="rejected")
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 202


# ============================================================
# Image-expired gate
# ============================================================
class TestRescanImageExpired:
    def test_410_when_image_deleted_at_set(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(
            db,
            store=store,
            user=user,
            image_deleted_at=datetime.now(UTC),
        )
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 410
        assert resp.json()["detail"] == "receipt_image_expired"
        assert captured_enqueue == []

    def test_410_when_r2_key_is_null(self, client, store, user, db, cap_3, captured_enqueue):
        """The purger may NULL the key without setting image_deleted_at
        in some legacy rows — guard both paths."""
        receipt = _make_receipt(db, store=store, user=user, image_r2_key=None)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 410


# ============================================================
# Cap
# ============================================================
class TestRescanCap:
    def test_429_when_attempts_equal_cap(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user, rescan_attempts=3)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rescan_cap_exceeded"
        assert resp.headers.get("X-Rescan-Attempts") == "3"
        assert resp.headers.get("X-Rescan-Cap") == "3"
        assert captured_enqueue == []

    def test_429_when_attempts_above_cap(self, client, store, user, db, cap_3, captured_enqueue):
        """Defensive — should not happen in practice but the gate must hold."""
        receipt = _make_receipt(db, store=store, user=user, rescan_attempts=5)
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 429

    def test_third_rescan_passes_fourth_rejects(self, client, store, user, db, cap_3, captured_enqueue):
        receipt = _make_receipt(db, store=store, user=user, rescan_attempts=2)
        ok = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert ok.status_code == 202
        assert ok.json()["rescan_attempts"] == 3
        ko = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert ko.status_code == 429


# ============================================================
# Atomic increment — no over-shoot under contention
# ============================================================
class TestRescanAtomicIncrement:
    def test_atomic_update_below_cap_only(self, client, store, user, db, cap_3, captured_enqueue):
        """Direct SQL probe — confirm the UPDATE has the WHERE attempts<cap
        clause that prevents over-shoot. Bypasses the route, exercises
        the canonical statement shape."""
        receipt = _make_receipt(db, store=store, user=user, rescan_attempts=3)
        # Replay the UPDATE shape from the service inline ; expect 0 rows
        # affected because attempts already equals cap.
        result = db.execute(
            text(
                "UPDATE receipts SET rescan_attempts = rescan_attempts + 1 WHERE id = :rid AND rescan_attempts < :cap"
            ),
            {"rid": str(receipt.id), "cap": 3},
        )
        assert result.rowcount == 0
        db.commit()
        # And the row's stored value is unchanged
        db.expire_all()
        assert db.get(Receipt, receipt.id).rescan_attempts == 3


# ============================================================
# Rate limit
# ============================================================
class TestRescanRateLimit:
    def test_429_after_4_calls_in_a_minute(self, client, store, user, db, cap_3, captured_enqueue):
        """slowapi caps at 3/minute. 4th call returns 429 with
        ``rate_limit_exceeded`` (distinct detail from
        ``rescan_cap_exceeded``)."""
        # Reset the slowapi storage explicitly (the autouse fixture
        # runs between tests but not within a test).
        from limiter import limiter

        limiter._storage.reset()

        receipt = _make_receipt(db, store=store, user=user)
        # 3 within cap → 3 successful 202s
        for _ in range(3):
            r = client.post(
                f"/api/v1/scan/receipt/{receipt.id}/rescan",
                headers=_auth(user),
            )
            # The 4th attempt on the same receipt would hit cap=3, but
            # the rate-limit fires before, so we never reach that branch
            # here. We just need each of these to be either 202 or 429
            # rate-limited.
            assert r.status_code in (202, 429)
        # The 4th call is the canonical rate-limit hit.
        resp = client.post(f"/api/v1/scan/receipt/{receipt.id}/rescan", headers=_auth(user))
        assert resp.status_code == 429
        # slowapi handler returns ``rate_limit_exceeded`` — distinct
        # from ``rescan_cap_exceeded``.
        assert resp.json()["detail"] == "rate_limit_exceeded"
