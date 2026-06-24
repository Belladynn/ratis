"""Cashback gating — defense in depth on store.validation_status (PR-B).

The PA worker must NOT trigger cashback for a receipt unless BOTH:
- ``receipt.store_status == 'confirmed'`` (existing gate)
- ``store.validation_status == 'confirmed'`` (new gate, PR-B)

This second gate keeps user_suggested stores in pending/suspicious states from
paying cashback before the consensus batch flips them to confirmed.

The gate lives in ``worker.receipt_task._award_scan_rewards`` ; these tests
drive it through ``process_receipt`` with a DB-only ``run_pipeline`` stub.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from ratis_core.models.scan import Receipt
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text


def _fake_image() -> np.ndarray:
    img = np.full((200, 400, 3), 240, dtype=np.uint8)
    cv2.putText(img, "RECEIPT", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    return img


def _mock_s3(image: np.ndarray) -> MagicMock:
    _, buf = cv2.imencode(".jpg", image)
    image_bytes = buf.tobytes()

    def download_fileobj(bucket, key, fileobj):
        fileobj.write(image_bytes)

    s3 = MagicMock()
    s3.download_fileobj.side_effect = download_fileobj
    return s3


@pytest.fixture
def user_with_balance(db) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email="cashback_user@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _make_receipt(db, *, store, user, store_status="confirmed") -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        purchased_at=date.today(),
        image_r2_key="cb-gating.jpg",
        store_status=store_status,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _set_store_validation(db, store: Store, status: str) -> None:
    store.validation_status = status
    db.flush()
    db.commit()


def _stub_pipeline_matched(monkeypatch, *, product_ean: str) -> None:
    """Stub ``run_pipeline`` with a DB-only fake that inserts one
    matched scan + confirms the receipt store (when a store is set)."""

    def _fake_run_pipeline(
        image_bytes,
        *,
        db,
        user_id,
        captured_at=None,
        receipt_id=None,
        log_level="normal",
    ):
        row = db.execute(
            text("SELECT store_id FROM receipts WHERE id = :rid"),
            {"rid": receipt_id},
        ).first()
        store_id = row.store_id if row is not None else None
        scan_store_status = "confirmed" if store_id is not None else "unknown"
        db.execute(
            text(
                "UPDATE receipts SET store_status = 'confirmed', "
                "  updated_at = now() WHERE id = :rid AND store_id IS NOT NULL"
            ),
            {"rid": receipt_id},
        )
        db.execute(
            text(
                "INSERT INTO scans "
                "(id, user_id, store_id, store_status, product_ean, "
                " scanned_name, price, quantity, scan_type, receipt_id, "
                " status, match_method, match_confidence, scanned_at, "
                " status_updated_at) "
                "VALUES (:id, :uid, :store, :store_status, :ean, "
                "        'NUTELLA 400G', 250, 1, 'receipt', :rid, "
                "        'matched', 'barcode', 1.0, now(), now())"
            ),
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "store": store_id,
                "store_status": scan_store_status,
                "ean": product_ean,
                "rid": receipt_id,
            },
        )
        return {
            "receipt_id": receipt_id,
            "parsed_ticket_id": None,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        }

    import worker.pipeline.orchestrator as pipeline_orch

    monkeypatch.setattr(pipeline_orch, "run_pipeline", _fake_run_pipeline)


def _run(db, receipt, monkeypatch) -> list:
    """Run process_receipt with mocks, neutralising network side-effects.
    Returns the cashback_called list (mutated by the patched callable)."""
    import worker.receipt_task as task_module
    from worker.receipt_task import process_receipt

    cashback_called: list = []
    monkeypatch.setattr(
        task_module,
        "trigger_cashback_scan",
        lambda *a, **kw: cashback_called.append(True),
    )
    monkeypatch.setattr(task_module, "trigger_action", lambda *a, **kw: None)
    process_receipt.apply(
        args=[str(receipt.id)],
        kwargs={
            "_s3": _mock_s3(_fake_image()),
            "_db": db,
        },
    )
    db.expire_all()
    return cashback_called


class TestCashbackGatingValidationStatus:
    def test_confirmed_store_with_confirmed_validation_triggers_cashback(
        self, db, store, product, user_with_balance, monkeypatch
    ):
        """Receipt confirmed + store validation confirmed + matched scan
        → cashback fires."""
        _set_store_validation(db, store, "confirmed")
        _stub_pipeline_matched(monkeypatch, product_ean=product.ean)
        receipt = _make_receipt(db, store=store, user=user_with_balance, store_status="confirmed")
        cashback_called = _run(db, receipt, monkeypatch)
        assert len(cashback_called) == 1

    def test_confirmed_store_with_pending_validation_blocks_cashback(
        self, db, store, product, user_with_balance, monkeypatch
    ):
        """Receipt confirmed but store still validation_status='pending'
        (e.g. user_suggested awaiting consensus) → cashback blocked."""
        _set_store_validation(db, store, "pending")
        _stub_pipeline_matched(monkeypatch, product_ean=product.ean)
        receipt = _make_receipt(db, store=store, user=user_with_balance, store_status="confirmed")
        cashback_called = _run(db, receipt, monkeypatch)
        assert len(cashback_called) == 0

    def test_confirmed_store_with_suspicious_validation_blocks_cashback(
        self, db, store, product, user_with_balance, monkeypatch
    ):
        """A store flipped to suspicious (pending too long) must never pay cashback."""
        _set_store_validation(db, store, "suspicious")
        _stub_pipeline_matched(monkeypatch, product_ean=product.ean)
        receipt = _make_receipt(db, store=store, user=user_with_balance, store_status="confirmed")
        cashback_called = _run(db, receipt, monkeypatch)
        assert len(cashback_called) == 0

    def test_unknown_store_blocks_cashback(self, db, product, user_with_balance, monkeypatch):
        """Receipt with no resolved store (store_id=None, store_status='unknown')
        never pays cashback — defense in depth on top of the store_id gate."""
        _stub_pipeline_matched(monkeypatch, product_ean=product.ean)

        receipt = Receipt(
            id=uuid.uuid4(),
            user_id=user_with_balance.id,
            store_id=None,
            purchased_at=date.today(),
            image_r2_key="cb-gating-unknown.jpg",
            store_status="unknown",
        )
        db.add(receipt)
        db.flush()
        db.commit()

        cashback_called = _run(db, receipt, monkeypatch)
        assert len(cashback_called) == 0
        db.refresh(receipt)
        assert receipt.store_id is None
        assert receipt.store_status == "unknown"
