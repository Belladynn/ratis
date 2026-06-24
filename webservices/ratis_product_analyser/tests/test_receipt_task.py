from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import cv2
import numpy as np
from ratis_core.models.scan import Receipt, Scan
from repositories.scan_repository import handle_barcode_rescan
from sqlalchemy import text

# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_image() -> np.ndarray:
    """200x400 white image with some text-like noise."""
    img = np.full((200, 400, 3), 240, dtype=np.uint8)
    cv2.putText(img, "RECEIPT", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    return img


def _mock_s3(image: np.ndarray) -> MagicMock:
    """S3 client that returns the given image on download_fileobj."""
    _, buf = cv2.imencode(".jpg", image)
    image_bytes = buf.tobytes()

    def download_fileobj(bucket, key, fileobj):
        fileobj.write(image_bytes)

    s3 = MagicMock()
    s3.download_fileobj.side_effect = download_fileobj
    return s3


# ── tests ─────────────────────────────────────────────────────────────────────


class TestHandleBarcodeRescan:
    """Tests for handle_barcode_rescan in scan_repository."""

    def test_returns_none_when_no_match(self, db, store, user):
        receipt = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=date.today(),
        )
        db.add(receipt)
        db.flush()
        result = handle_barcode_rescan(db, "234100109106250407120518", receipt.id)
        assert result is None

    def test_supersedes_old_receipt_scans(self, db, store, user):
        old_receipt = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=date.today(),
            receipt_barcode="234100109106250407120518",
            store_status="confirmed",
        )
        db.add(old_receipt)
        db.flush()
        old_scan = Scan(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            receipt_id=old_receipt.id,
            scan_type="receipt",
            status="accepted",
            scanned_name="NUTELLA",
            price=250,
        )
        db.add(old_scan)
        db.commit()

        new_receipt = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=date.today(),
        )
        db.add(new_receipt)
        db.flush()

        old_id = handle_barcode_rescan(db, "234100109106250407120518", new_receipt.id)
        assert old_id == old_receipt.id

        db.refresh(old_scan)
        assert old_scan.status == "rejected"
        assert old_scan.rejected_reason == "superseded_rescan"

        db.refresh(old_receipt)
        assert old_receipt.receipt_barcode is None

    def test_does_not_match_same_receipt(self, db, store, user):
        receipt = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=date.today(),
            receipt_barcode="234100109106250407120518",
        )
        db.add(receipt)
        db.flush()
        result = handle_barcode_rescan(db, "234100109106250407120518", receipt.id)
        assert result is None

    def test_select_uses_for_update(self, monkeypatch):
        """handle_barcode_rescan must issue SELECT … FOR UPDATE to prevent double-supersede
        when two Celery workers process the same barcode concurrently (DP-10)."""
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.orm import Session as SASession

        captured: list[str] = []

        db_mock = MagicMock(spec=SASession)

        def _scalar(stmt, **kw):
            # Compile the statement against PostgreSQL dialect to get the actual SQL text
            compiled = stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": False},
            )
            captured.append(str(compiled))
            return None  # simulate "no old receipt found"

        db_mock.scalar.side_effect = _scalar

        barcode = "234100109106250407120518"
        new_id = uuid.uuid4()
        result = handle_barcode_rescan(db_mock, barcode, new_id)

        assert result is None, "Expected None when no old receipt found"
        assert len(captured) == 1, "db.scalar must be called exactly once"
        assert "FOR UPDATE" in captured[0].upper(), (
            "handle_barcode_rescan must use .with_for_update() on the SELECT "
            f"to prevent race conditions (DP-10). Got: {captured[0]!r}"
        )


class TestPipelineBranch:
    """End-to-end checks on the V3 pipeline in ``process_receipt``.

    These tests stub ``run_pipeline`` with a DB-only fake that creates
    a matched receipt / scan to mirror the post-persist state (covers
    ``persist_pipeline_result`` contract without paying the full OCR +
    LLM cost), then assert that the tail of ``process_receipt`` :

    - F-PA-1 : awards CAB + XP via ``trigger_action`` /
      ``trigger_cashback_scan`` for accepted scans behind a confirmed store.
    - F-PA-5 : reconciles prior 'unknown' label scans for the user via
      ``reconcile_unknown_scans_for_receipt``.
    """

    def _stub_pipeline_run(
        self,
        monkeypatch,
        *,
        product_ean: str | None = None,
        scan_status: str = "matched",
        match_method: str | None = "barcode",
        price_cents: int = 250,
        scanned_name: str = "NUTELLA 400G",
    ):
        """Stub ``run_pipeline`` with a DB-only fake that inserts the
        receipt-tail state V3 would produce on a real run :

        - Sets ``receipt.store_status='confirmed'`` (store already attached
          on the fixture) ;
        - Inserts one ``scans`` row with the requested status / method /
          EAN. Mirrors what ``persist._insert_scan`` does for a matched
          item — minus the parsed_tickets row (not needed for the tail
          tests).
        """

        def _fake_run_pipeline(
            image_bytes,
            *,
            db,
            user_id,
            captured_at=None,
            receipt_id=None,
            log_level="normal",
        ):
            # Mirror persist : confirm receipt store_status + insert scan row.
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
                    " status, match_method, match_confidence, rejected_reason, "
                    " scanned_at, status_updated_at) "
                    "VALUES (:id, :uid, :store, :store_status, :ean, :name, "
                    "        :price, 1, 'receipt', :rid, :status, :method, "
                    "        1.0, :reason, now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": user_id,
                    "store": store_id,
                    "store_status": scan_store_status,
                    "ean": product_ean,
                    "name": scanned_name,
                    "price": price_cents,
                    "rid": receipt_id,
                    "status": scan_status,
                    "method": match_method,
                    "reason": (None if scan_status == "matched" else "no_fuzzy_candidate"),
                },
            )
            return {
                "receipt_id": receipt_id,
                "parsed_ticket_id": None,
                "scan_ids": [],
                "store_candidate_id": None,
                "audit_event_count": 0,
            }

        # Patch the import inside ``worker.pipeline.orchestrator`` since
        # ``receipt_task`` does a late ``from worker.pipeline.orchestrator
        # import run_pipeline`` inside the pipeline branch.
        import worker.pipeline.orchestrator as pipeline_orch

        monkeypatch.setattr(pipeline_orch, "run_pipeline", _fake_run_pipeline)

    def _run(self, db, receipt):
        from worker.receipt_task import process_receipt

        process_receipt.apply(
            args=[str(receipt.id)],
            kwargs={
                "_s3": _mock_s3(_fake_image()),
                "_db": db,
            },
        )
        db.expire_all()

    # ── F-PA-1 — grants ──────────────────────────────────────────────────────

    def test_pipeline_matched_scan_triggers_action_and_cashback(self, db, store, product, user, monkeypatch):
        """V3 path with one matched scan + confirmed store → fires
        ``trigger_action('receipt_scan')`` AND ``trigger_cashback_scan``."""
        self._stub_pipeline_run(monkeypatch, product_ean=product.ean)

        actions: list[dict] = []
        cashback_calls: list[list[dict]] = []
        monkeypatch.setattr(
            "worker.receipt_task.trigger_action",
            lambda uid, action_type, **kw: actions.append({"user_id": uid, "action_type": action_type, **kw}),
        )
        monkeypatch.setattr(
            "worker.receipt_task.trigger_cashback_scan",
            lambda uid, lines: cashback_calls.append(lines),
        )

        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            purchased_at=date.today(),
            image_r2_key="fake-key.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()

        self._run(db, r)

        assert len(actions) == 1, f"expected 1 trigger_action call, got {actions!r}"
        assert actions[0]["action_type"] == "receipt_scan"
        assert actions[0]["user_id"] == user.id
        assert actions[0]["idempotency_key"] == str(r.id)

        assert len(cashback_calls) == 1
        lines = cashback_calls[0]
        assert len(lines) == 1
        assert lines[0]["ean"] == product.ean
        assert lines[0]["price"] == 250

    def test_pipeline_unmatched_scan_does_not_trigger_cashback(self, db, store, user, monkeypatch):
        """V3 path that yields an unresolved scan (no EAN) → no cashback
        and no action."""
        self._stub_pipeline_run(
            monkeypatch,
            product_ean=None,
            scan_status="unresolved",
            match_method=None,
        )

        actions: list = []
        cashback_calls: list = []
        monkeypatch.setattr(
            "worker.receipt_task.trigger_action",
            lambda *a, **kw: actions.append(1),
        )
        monkeypatch.setattr(
            "worker.receipt_task.trigger_cashback_scan",
            lambda *a, **kw: cashback_calls.append(1),
        )

        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            purchased_at=date.today(),
            image_r2_key="fake-key.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()

        self._run(db, r)

        assert actions == []
        assert cashback_calls == []

    # ── F-PA-5 — reconciliation Part B ───────────────────────────────────────

    def _seed_unknown_scan_near(self, db, user, store) -> Scan:
        """Create an 'unknown' label scan within 100m of the given store."""
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=None,
            store_status="unknown",
            scan_type="electronic_label",
            scanned_name="NUTELLA 400G",
            price=250,
            quantity=1.0,
            status="pending",
            user_lat=Decimal(str(float(store.lat) + 0.00005)),  # ~5m north
            user_lng=Decimal(str(float(store.lng) + 0.00005)),
            scanned_at=datetime.now(UTC) - timedelta(days=1),
        )
        db.add(scan)
        db.flush()
        db.commit()
        return scan

    def test_pipeline_reconciles_prior_unknown_label_scans(self, db, store, product, user, monkeypatch):
        """V3 path must reconcile prior unknown label scans for the user.

        F-PA-5 — the receipt tail runs ``reconcile_unknown_scans_for_receipt``
        once the store is confirmed, so pending 'unknown' label scans get
        attached and their user_lat/user_lng PII cleared.
        """
        self._stub_pipeline_run(monkeypatch, product_ean=product.ean)

        # Avoid HTTP side effects from reconciliation's reward trigger
        # AND from the grants tail. Both are fire-and-forget — we are
        # checking the DB-side mutations here, not the rewards events.
        monkeypatch.setattr("worker.receipt_task.trigger_action", lambda *a, **k: None)
        monkeypatch.setattr("worker.receipt_task.trigger_cashback_scan", lambda *a, **k: None)
        monkeypatch.setattr(
            "services.reconciliation_service._default_reward_trigger",
            lambda *a, **k: None,
        )

        unknown_scan = self._seed_unknown_scan_near(db, user, store)

        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            purchased_at=date.today(),
            image_r2_key="fake-key.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()

        self._run(db, r)

        db.refresh(unknown_scan)
        assert unknown_scan.store_id == store.id
        assert unknown_scan.store_status == "confirmed"
        # PII cleared once reconciliation served its purpose.
        assert unknown_scan.user_lat is None
        assert unknown_scan.user_lng is None

    def test_pipeline_no_reconcile_when_store_unconfirmed(self, db, store, product, user, monkeypatch):
        """Reconciliation is gated on ``store_status='confirmed'`` — a V3
        run that leaves the receipt with another status must not touch
        the user's unknown label scans (their geo-radius is the only
        signal we can trust)."""

        def _fake_run_pipeline_unconfirmed(
            image_bytes,
            *,
            db,
            user_id,
            captured_at=None,
            receipt_id=None,
            log_level="normal",
        ):
            # Leave receipt.store_status as default ('unknown' / 'pending') —
            # no scans inserted. Production case : OCR failed to resolve a store.
            return {
                "receipt_id": receipt_id,
                "parsed_ticket_id": None,
                "scan_ids": [],
                "store_candidate_id": None,
                "audit_event_count": 0,
            }

        import worker.pipeline.orchestrator as pipeline_orch

        monkeypatch.setattr(pipeline_orch, "run_pipeline", _fake_run_pipeline_unconfirmed)

        monkeypatch.setattr("worker.receipt_task.trigger_action", lambda *a, **k: None)
        monkeypatch.setattr("worker.receipt_task.trigger_cashback_scan", lambda *a, **k: None)
        monkeypatch.setattr(
            "services.reconciliation_service._default_reward_trigger",
            lambda *a, **k: None,
        )

        unknown_scan = self._seed_unknown_scan_near(db, user, store)

        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=None,
            purchased_at=date.today(),
            image_r2_key="fake-key.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()

        self._run(db, r)

        db.refresh(unknown_scan)
        # Scan stayed unknown — no reconciliation occurred.
        assert unknown_scan.store_id is None
        assert unknown_scan.store_status == "unknown"

    def test_pipeline_store_unconfirmed_blocks_cashback(self, db, store, product, user, monkeypatch):
        """Defense-in-depth : even if a scan is matched, an unconfirmed
        store blocks the grants."""
        # Force the store to 'pending' validation
        store.validation_status = "pending"
        db.flush()
        db.commit()

        self._stub_pipeline_run(monkeypatch, product_ean=product.ean)

        actions: list = []
        cashback_calls: list = []
        monkeypatch.setattr(
            "worker.receipt_task.trigger_action",
            lambda *a, **kw: actions.append(1),
        )
        monkeypatch.setattr(
            "worker.receipt_task.trigger_cashback_scan",
            lambda *a, **kw: cashback_calls.append(1),
        )

        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            purchased_at=date.today(),
            image_r2_key="fake-key.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()

        self._run(db, r)

        assert actions == []
        assert cashback_calls == []


class TestObserveTracingOff(TestPipelineBranch):
    """``process_receipt`` is decorated with ``@observe`` (Langfuse, DA-LO3).

    With the Langfuse keys empty (conftest forces ``LANGFUSE_*=""`` → tracing
    no-op), the decorator must be completely transparent : the task still runs
    end-to-end and produces the same grants. Zero network — the real Anthropic
    client is never reached (``run_pipeline`` is stubbed) and no Langfuse
    client is initialised. Inherits the stub helpers from ``TestPipelineBranch``.
    """

    def test_process_receipt_is_observe_wrapped(self):
        """Structural : ``@observe`` wraps the task callable (functools.wraps
        exposes ``__wrapped__`` under the Celery task)."""
        from worker.receipt_task import process_receipt

        assert hasattr(process_receipt.run, "__wrapped__"), (
            "process_receipt should be wrapped by @observe (Langfuse tracing, "
            "DA-LO3) — expected functools.wraps __wrapped__ on the task callable"
        )

    def test_decorated_task_runs_with_tracing_off(self, db, store, product, user, monkeypatch):
        """The decorated task completes normally when Langfuse is disabled
        (empty keys) — the @observe decorator is inert without a client."""
        # Belt-and-braces : assert tracing is actually off in this run so the
        # test proves the no-op path, not an accidentally-configured client.
        import os

        assert os.environ.get("LANGFUSE_PUBLIC_KEY", "") == ""
        assert os.environ.get("LANGFUSE_SECRET_KEY", "") == ""

        self._stub_pipeline_run(monkeypatch, product_ean=product.ean)

        actions: list[dict] = []
        cashback_calls: list = []
        monkeypatch.setattr(
            "worker.receipt_task.trigger_action",
            lambda uid, action_type, **kw: actions.append({"action_type": action_type}),
        )
        monkeypatch.setattr(
            "worker.receipt_task.trigger_cashback_scan",
            lambda uid, lines: cashback_calls.append(lines),
        )

        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            purchased_at=date.today(),
            image_r2_key="fake-key.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()

        # Must not raise through the @observe wrapper, and the tail must run.
        self._run(db, r)

        assert actions == [{"action_type": "receipt_scan"}]
        assert len(cashback_calls) == 1
