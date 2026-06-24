from __future__ import annotations

import base64
import hashlib
import io
import uuid
from datetime import UTC
from decimal import Decimal
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from ratis_core.models.scan import LabelSession, Scan
from ratis_core.models.user import User

from tests.conftest import make_token

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVQI12NgAAAAAgAB4iG8MwAAAABJRU5ErkJggg=="
)
_WEBP_BYTES = b"RIFF\x20\x00\x00\x00WEBP" + b"\x00" * 100
_PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 100
_EXE_BYTES = b"MZ" + b"\x00" * 100


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _label_payload(content=None, content_type="image/jpeg", filename="label.jpg"):
    return {"image": (filename, io.BytesIO(content or _JPEG_BYTES), content_type)}


# ============================================================
# POST /api/v1/scan/label
# ============================================================


class TestPostScanLabel:
    def test_returns_202_and_scan_id(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files=_label_payload(),
            headers=_auth(user),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "scan_id" in body
        uuid.UUID(body["scan_id"])

    def test_scan_created_in_db(self, client, store, user, db):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files=_label_payload(),
            headers=_auth(user),
        )
        scan_id = uuid.UUID(resp.json()["scan_id"])
        scan = db.get(Scan, scan_id)
        assert scan is not None
        assert scan.store_id == store.id
        assert scan.user_id == user.id
        assert scan.scan_type == "electronic_label"
        assert scan.status == "pending"
        assert scan.label_r2_key == f"label/{scan_id}.jpg"
        assert scan.label_session_id is None

    def test_hint_receipt_accepted(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id), "hint": "receipt"},
            files=_label_payload(),
            headers=_auth(user),
        )
        assert resp.status_code == 202

    def test_store_not_found_returns_404(self, client, user):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(uuid.uuid4())},
            files=_label_payload(),
            headers=_auth(user),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "store_not_found"

    def test_disabled_store_returns_404(self, client, store, user, db):
        # PG ``disabled_at_check`` : set both columns together.
        from datetime import datetime

        store.is_disabled = True
        store.disabled_at = datetime.now(UTC)
        db.flush()
        db.commit()
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files=_label_payload(),
            headers=_auth(user),
        )
        assert resp.status_code == 404

    def test_missing_image_returns_422(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_missing_store_id_returns_422(self, client, user):
        resp = client.post(
            "/api/v1/scan/label",
            files=_label_payload(),
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_no_token_returns_401(self, client, store):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files=_label_payload(),
        )
        assert resp.status_code == 401

    def test_pdf_rejected(self, client, store, user):
        """PDF not accepted for labels — images only."""
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files={"image": ("label.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")},
            headers=_auth(user),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_spoofed_exe_as_jpeg_returns_422(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files={"image": ("label.jpg", io.BytesIO(_EXE_BYTES), "image/jpeg")},
            headers=_auth(user),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_png_accepted(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files=_label_payload(_PNG_BYTES, "image/png", "label.png"),
            headers=_auth(user),
        )
        assert resp.status_code == 202

    def test_queue_failure_returns_503(self, db, store, user, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app
        from ratis_core.database import get_db

        monkeypatch.setattr("services.label_service.upload_label_image", lambda *a, **kw: None)
        monkeypatch.setattr(
            "services.label_service.enqueue_label_job",
            MagicMock(side_effect=RuntimeError("Redis down")),
        )

        def override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_db
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/api/v1/scan/label",
                    data={"store_id": str(store.id)},
                    files=_label_payload(),
                    headers=_auth(user),
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 503
        assert resp.json()["detail"] == "queue_unavailable"


# ============================================================
# POST /api/v1/scan/label/batch
# ============================================================


class TestPostScanLabelBatch:
    """The frontend now sends user_lat/user_lng instead of store_id — the
    backend geo-matches to the nearest active store within the user's
    search_radius_km preference."""

    def _multi_payload(self, n: int = 3):
        # Append a unique suffix to each image so hashes differ across the batch.
        # Magic bytes (\xff\xd8\xff\xe0) are at the start — suffix doesn't affect mime detection.
        return [("images", (f"label_{i}.jpg", io.BytesIO(_JPEG_BYTES + bytes([i])), "image/jpeg")) for i in range(n)]

    def _geo(self, store) -> dict:
        """Geo payload pointing exactly at the fixture store."""
        return {"user_lat": str(store.lat), "user_lng": str(store.lng)}

    def test_returns_202_session_id_and_scan_ids(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label/batch",
            data=self._geo(store),
            files=self._multi_payload(3),
            headers=_auth(user),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "session_id" in body
        assert "scan_ids" in body
        assert body["store_status"] == "confirmed"
        uuid.UUID(body["session_id"])
        assert len(body["scan_ids"]) == 3
        for sid in body["scan_ids"]:
            uuid.UUID(sid)

    def test_uses_nearest_store_from_geo(self, client, store, user, db):
        """Backend picks the nearest active store within search_radius_km."""
        resp = client.post(
            "/api/v1/scan/label/batch",
            data=self._geo(store),
            files=self._multi_payload(2),
            headers=_auth(user),
        )
        assert resp.json()["store_status"] == "confirmed"
        session_id = uuid.UUID(resp.json()["session_id"])
        session = db.get(LabelSession, session_id)
        assert session is not None
        assert session.store_id == store.id
        assert session.user_id == user.id
        assert session.scan_count == 2

    def test_all_scans_linked_to_session(self, client, store, user, db):
        resp = client.post(
            "/api/v1/scan/label/batch",
            data=self._geo(store),
            files=self._multi_payload(3),
            headers=_auth(user),
        )
        body = resp.json()
        session_id = uuid.UUID(body["session_id"])
        for sid_str in body["scan_ids"]:
            scan = db.get(Scan, uuid.UUID(sid_str))
            assert scan is not None
            assert scan.label_session_id == session_id
            assert scan.status == "pending"
            assert scan.scan_type == "electronic_label"
            assert scan.store_status == "confirmed"
            # Confirmed scans don't persist user geo — it's redundant with store_id
            assert scan.user_lat is None
            assert scan.user_lng is None

    def test_empty_images_returns_422(self, client, store, user):
        resp = client.post(
            "/api/v1/scan/label/batch",
            data=self._geo(store),
            files=[],
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_no_store_in_radius_saves_as_unknown(self, client, user, db):
        """Geo point far from any active store → 202 with store_status='unknown'.

        Fire-and-forget: we never 404 on a label batch. The scan is
        persisted with store_id=NULL, store_status='unknown', no CAB/XP
        awarded. Part B reconciles these against a future receipt.
        """
        resp = client.post(
            "/api/v1/scan/label/batch",
            # Middle of the Atlantic, nowhere near the fixture (Paris).
            data={"user_lat": "0.0", "user_lng": "-30.0"},
            files=self._multi_payload(2),
            headers=_auth(user),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["store_status"] == "unknown"
        assert len(body["scan_ids"]) == 2
        session_id = uuid.UUID(body["session_id"])

        # Session persisted with store_id=NULL
        session = db.get(LabelSession, session_id)
        assert session is not None
        assert session.store_id is None
        assert session.scan_count == 2

        # Each scan persisted with store_id=NULL + store_status='unknown'
        # + user_lat/user_lng populated for Part B reconciliation.
        for sid_str in body["scan_ids"]:
            scan = db.get(Scan, uuid.UUID(sid_str))
            assert scan is not None
            assert scan.store_id is None
            assert scan.store_status == "unknown"
            assert scan.user_lat is not None
            assert scan.user_lng is not None
            assert scan.status == "pending"

    def test_unknown_store_does_not_enqueue_ocr(self, db, user, monkeypatch):
        """No OCR / CAB triggered when store is unknown — worker is not called."""
        enqueue_calls: list[uuid.UUID] = []
        monkeypatch.setattr("services.label_service.upload_label_image", lambda *a, **kw: None)
        monkeypatch.setattr(
            "services.label_service.enqueue_label_job",
            lambda scan_id, hint="label": enqueue_calls.append(scan_id),
        )

        from main import app
        from ratis_core.database import get_db

        app.dependency_overrides[get_db] = lambda: (yield db)
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/api/v1/scan/label/batch",
                    data={"user_lat": "0.0", "user_lng": "-30.0"},
                    files=self._multi_payload(2),
                    headers=_auth(user),
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 202
        assert resp.json()["store_status"] == "unknown"
        assert enqueue_calls == []  # no worker triggered for unknown-store scans

    def test_missing_lat_lng_returns_422(self, client, user):
        resp = client.post(
            "/api/v1/scan/label/batch",
            files=self._multi_payload(1),
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_no_token_returns_401(self, client, store):
        resp = client.post(
            "/api/v1/scan/label/batch",
            data=self._geo(store),
            files=self._multi_payload(1),
        )
        assert resp.status_code == 401


# ============================================================
# GET /api/v1/scan/label/session/{session_id}
# ============================================================


class TestGetLabelSession:
    def _create_session(self, db, store, user, n=2) -> LabelSession:
        s = LabelSession(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            scan_count=n,
        )
        db.add(s)
        db.flush()
        db.commit()
        return s

    def _add_scan(self, db, store, user, session, status="pending"):
        scan = Scan(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            scan_type="electronic_label",
            status=status,
            price=Decimal("1.99"),
            quantity=Decimal("1"),
            label_session_id=session.id,
        )
        db.add(scan)
        db.flush()
        db.commit()
        return scan

    def test_processing_when_scan_pending(self, client, store, user, db):
        session = self._create_session(db, store, user, n=2)
        self._add_scan(db, store, user, session, status="pending")
        resp = client.get(f"/api/v1/scan/label/session/{session.id}", headers=_auth(user))
        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"

    def test_processing_when_partial(self, client, store, user, db):
        session = self._create_session(db, store, user, n=2)
        self._add_scan(db, store, user, session, status="accepted")
        self._add_scan(db, store, user, session, status="pending")
        resp = client.get(f"/api/v1/scan/label/session/{session.id}", headers=_auth(user))
        assert resp.json()["status"] == "processing"

    def test_done_when_all_terminal(self, client, store, user, db):
        session = self._create_session(db, store, user, n=2)
        self._add_scan(db, store, user, session, status="accepted")
        self._add_scan(db, store, user, session, status="unmatched")
        resp = client.get(f"/api/v1/scan/label/session/{session.id}", headers=_auth(user))
        body = resp.json()
        assert body["status"] == "done"
        assert body["products_identified"] == 1  # only accepted count

    def test_done_response_has_only_status_and_products_identified(self, client, store, user, db):
        """Response shape: only status + products_identified — no raw counts exposed."""
        session = self._create_session(db, store, user, n=1)
        self._add_scan(db, store, user, session, status="accepted")
        resp = client.get(f"/api/v1/scan/label/session/{session.id}", headers=_auth(user))
        body = resp.json()
        assert set(body.keys()) == {"status", "products_identified"}

    def test_not_found_returns_404(self, client, user):
        resp = client.get(f"/api/v1/scan/label/session/{uuid.uuid4()}", headers=_auth(user))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "label_session_not_found"

    def test_other_user_returns_403(self, client, store, user, db):
        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            account_type="oauth",
            is_deleted=False,
        )
        db.add(other)
        db.flush()
        session = self._create_session(db, store, other, n=1)
        resp = client.get(f"/api/v1/scan/label/session/{session.id}", headers=_auth(user))
        assert resp.status_code == 403

    def test_no_token_returns_401(self, client, store, user, db):
        session = self._create_session(db, store, user)
        resp = client.get(f"/api/v1/scan/label/session/{session.id}")
        assert resp.status_code == 401


# ============================================================
# Photo hash — déduplication étiquettes
# ============================================================

# ============================================================
# Rate limiting — POST /label + POST /label/batch
# ============================================================


class TestRateLimitLabel:
    def test_fourth_label_request_returns_429(self, client, store, user):
        images = [_JPEG_BYTES, _PNG_BYTES, _WEBP_BYTES, _JPEG_BYTES + b"\x04"]
        for img in images[:3]:
            client.post(
                "/api/v1/scan/label",
                data={"store_id": str(store.id)},
                files={"image": ("l.jpg", io.BytesIO(img), "image/jpeg")},
                headers=_auth(user),
            )
        resp = client.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files={"image": ("l.jpg", io.BytesIO(images[3]), "image/jpeg")},
            headers=_auth(user),
        )
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limit_exceeded"

    def test_fourth_batch_request_returns_429(self, client, store, user):
        geo = {"user_lat": str(store.lat), "user_lng": str(store.lng)}
        for i in range(3):
            client.post(
                "/api/v1/scan/label/batch",
                data=geo,
                files=[("images", (f"l{i}.jpg", io.BytesIO(_JPEG_BYTES + bytes([i])), "image/jpeg"))],
                headers=_auth(user),
            )
        resp = client.post(
            "/api/v1/scan/label/batch",
            data=geo,
            files=[("images", ("l3.jpg", io.BytesIO(_JPEG_BYTES + b"\x03"), "image/jpeg"))],
            headers=_auth(user),
        )
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limit_exceeded"


class TestPhotoHashLabel:
    def _post(self, c, store, user, content=None):
        return c.post(
            "/api/v1/scan/label",
            data={"store_id": str(store.id)},
            files={"image": ("label.jpg", io.BytesIO(content or _JPEG_BYTES), "image/jpeg")},
            headers=_auth(user),
        )

    def test_duplicate_label_returns_409(self, client, store, user):
        self._post(client, store, user)
        resp = self._post(client, store, user)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "duplicate_photo"

    def test_different_bytes_accepted(self, client, store, user):
        self._post(client, store, user, _JPEG_BYTES)
        resp = self._post(client, store, user, _PNG_BYTES)
        assert resp.status_code == 202

    def test_concurrent_photo_hash_violation_returns_409(self, client, store, user, monkeypatch):
        """A concurrent label upload of the same photo loses the check-first
        race and hits the ``scans_photo_hash_unique`` index on flush. That
        IntegrityError must surface as 409 ``duplicate_photo``, not 500."""
        from sqlalchemy.exc import IntegrityError

        def _raise_unique(*a, **kw):
            raise IntegrityError(
                "INSERT INTO scans ...",
                {},
                Exception('duplicate key value violates unique constraint "scans_photo_hash_unique"'),
            )

        monkeypatch.setattr("services.label_service.create_label_scan", _raise_unique)
        resp = self._post(client, store, user)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "duplicate_photo"

    def test_photo_hash_stored_on_scan(self, client, store, user, db):
        expected = hashlib.sha256(_JPEG_BYTES).hexdigest()
        resp = self._post(client, store, user)
        scan = db.get(Scan, uuid.UUID(resp.json()["scan_id"]))
        assert scan.photo_hash == expected

    def test_r2_not_called_on_duplicate(self, db, store, user, monkeypatch):
        upload_calls = []
        monkeypatch.setattr(
            "services.label_service.upload_label_image",
            lambda *a, **kw: upload_calls.append(1),
        )
        monkeypatch.setattr("services.label_service.enqueue_label_job", lambda *a, **kw: None)

        from main import app
        from ratis_core.database import get_db

        app.dependency_overrides[get_db] = lambda: (yield db)
        try:
            with TestClient(app) as c:
                payload = {"store_id": str(store.id)}
                c.post(
                    "/api/v1/scan/label",
                    data=payload,
                    files={"image": ("l.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
                    headers=_auth(user),
                )
                count_after_first = len(upload_calls)
                c.post(
                    "/api/v1/scan/label",
                    data=payload,
                    files={"image": ("l.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
                    headers=_auth(user),
                )
        finally:
            app.dependency_overrides.clear()

        assert count_after_first == 1
        assert len(upload_calls) == 1
