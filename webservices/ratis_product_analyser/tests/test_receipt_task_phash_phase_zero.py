"""Integration tests — anti-fraud PR2 phase 0 pHash hook.

Cover :

- two users scan the same image → 2nd is rejected with
  ``rejected_reason='image_duplicate'`` + a ``fraud_suspicions`` row
  is created. OCR (``run_pipeline``) is NOT invoked.
- same user re-scans own image → phase 0 ignores the previous receipt
  (cross-user only), OCR proceeds (``run_pipeline`` invoked).
- pHash compute crashes → OCR continues (fail-safe verified). Same
  for lookup crashes.
- successful (non-duplicate) V3 run → ``receipts.image_phash`` is
  persisted afterwards so a future scan can match against it.

Cf. ``ARCH_receipt_pipeline.md`` § "Réconciliation tickets V1" step 2,
``webservices/ratis_product_analyser/worker/pipeline/phash.py``,
``worker/pipeline/phash_lookup.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from io import BytesIO
from unittest.mock import MagicMock

from PIL import Image
from ratis_core.models.fraud_suspicions import FraudSuspicion
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User
from sqlalchemy import text

# ─── helpers ────────────────────────────────────────────────────────────


def _make_image_bytes(seed: int = 0, size: int = 96) -> bytes:
    """Generate a deterministic JPEG payload — distinctive between seeds
    so phash compute is stable and reproducible."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for x in range(size):
        for y in range(size):
            r = (x * 3 + seed * 17) % 256
            g = (y * 5 + seed * 31) % 256
            b = ((x + y) + seed * 47) % 256
            px[x, y] = (r, g, b)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _s3_returning(payload: bytes) -> MagicMock:
    def download_fileobj(bucket, key, fileobj):
        fileobj.write(payload)

    s3 = MagicMock()
    s3.download_fileobj.side_effect = download_fileobj
    return s3


def _force_pipeline(monkeypatch) -> None:
    """No-op — kept so existing call sites stay valid.

    The receipt pipeline is V3-only ; there is no longer a routing
    decision to force. ``process_receipt`` always runs ``run_pipeline``.
    """


def _stub_pipeline_orchestrator_noop(monkeypatch) -> list[uuid.UUID]:
    """Replace ``run_pipeline`` with a no-op that only records calls.

    The pHash phase 0 logic runs BEFORE the orchestrator — so for the
    rejection-path tests we want to assert this stub was NEVER invoked
    (= OCR was skipped). For the happy-path tests it's invoked but
    intentionally a no-op (no scan row inserted) to keep the test
    focused on the pHash persistence rather than the full pipeline.
    """
    calls: list[uuid.UUID] = []

    def _fake_run(image_bytes, *, db, user_id, captured_at=None, receipt_id=None, log_level="normal", **_kw):
        calls.append(receipt_id)
        return {
            "receipt_id": receipt_id,
            "parsed_ticket_id": None,
            "scan_ids": [],
            "store_candidate_id": None,
            "audit_event_count": 0,
        }

    import worker.pipeline.orchestrator as pipeline_orch

    monkeypatch.setattr(pipeline_orch, "run_pipeline", _fake_run)

    # Also silence reward triggers — not under test here.
    monkeypatch.setattr("worker.receipt_task.trigger_action", lambda *a, **kw: None)
    monkeypatch.setattr("worker.receipt_task.trigger_cashback_scan", lambda *a, **kw: None)
    return calls


def _make_user(db, *, suffix: str) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"phash-it-{suffix}-{uid.hex[:8]}@ratis.fr",
        display_name="PhaseZeroUser",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _make_receipt(db, *, user_id: uuid.UUID | None, image_r2_key: str = "k") -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=None,
        purchased_at=date.today(),
        image_r2_key=image_r2_key,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _seed_peer_receipt_with_phash(
    db,
    *,
    user_id: uuid.UUID,
    phash_hex: str,
    created_at: datetime | None = None,
) -> uuid.UUID:
    rid = uuid.uuid4()
    if created_at is None:
        db.execute(
            text("INSERT INTO receipts (id, user_id, purchased_at, image_phash) VALUES (:id, :uid, CURRENT_DATE, :ph)"),
            {"id": rid, "uid": user_id, "ph": phash_hex},
        )
    else:
        db.execute(
            text(
                "INSERT INTO receipts "
                "(id, user_id, purchased_at, image_phash, created_at) "
                "VALUES (:id, :uid, CURRENT_DATE, :ph, :ts)"
            ),
            {"id": rid, "uid": user_id, "ph": phash_hex, "ts": created_at},
        )
    db.commit()
    return rid


def _run_task(db, receipt: Receipt, image_bytes: bytes) -> None:
    from worker.receipt_task import process_receipt

    process_receipt.apply(
        args=[str(receipt.id)],
        kwargs={
            "_s3": _s3_returning(image_bytes),
            "_db": db,
        },
    )
    db.expire_all()


# ─── tests ──────────────────────────────────────────────────────────────


class TestPhashPhaseZero:
    def test_cross_user_image_duplicate_is_rejected_before_ocr(self, db, monkeypatch):
        """Peer user previously scanned the same image (same pHash) →
        current scan is rejected with ``image_duplicate`` and OCR
        is skipped (orchestrator never invoked)."""
        _force_pipeline(monkeypatch)
        run_calls = _stub_pipeline_orchestrator_noop(monkeypatch)

        # Pre-compute the candidate hash to seed a peer receipt with it.
        from worker.pipeline.phash import compute_phash

        img_bytes = _make_image_bytes(seed=11)
        candidate_phash = compute_phash(img_bytes)
        assert candidate_phash is not None

        peer = _make_user(db, suffix="peer")
        me = _make_user(db, suffix="me")
        peer_receipt_id = _seed_peer_receipt_with_phash(db, user_id=peer.id, phash_hex=candidate_phash)

        my_receipt = _make_receipt(db, user_id=me.id, image_r2_key="img-me.jpg")

        _run_task(db, my_receipt, img_bytes)

        # OCR was skipped : the V3 orchestrator stub was never called.
        assert run_calls == [], f"expected OCR to be skipped on phase 0 rejection, got {run_calls!r}"

        # A marker scan is on the rejected receipt.
        scans = db.query(Scan).filter(Scan.receipt_id == my_receipt.id).all()
        assert len(scans) == 1
        assert scans[0].status == "rejected"
        assert scans[0].rejected_reason == "image_duplicate"
        assert scans[0].store_id is None
        assert scans[0].store_status == "unknown"

        # The receipt now carries the candidate pHash too (future scans
        # can match against this row).
        db.expire_all()
        my_receipt_refreshed = db.get(Receipt, my_receipt.id)
        assert my_receipt_refreshed.image_phash == candidate_phash

        # A fraud_suspicions row was created with signal='phash'.
        suspicions = db.query(FraudSuspicion).filter(FraudSuspicion.receipt_id == my_receipt.id).all()
        assert len(suspicions) == 1
        sus = suspicions[0]
        assert sus.detection_signal == "phash"
        assert sus.resolution_status == "pending"
        assert peer_receipt_id in sus.evidence_receipt_ids

    def test_same_user_rescans_own_image_does_not_block(self, db, monkeypatch):
        """The same user uploading the same image must not trigger the
        cross-user phase 0 rejection — that path is for OTHER users."""
        _force_pipeline(monkeypatch)
        run_calls = _stub_pipeline_orchestrator_noop(monkeypatch)

        from worker.pipeline.phash import compute_phash

        img_bytes = _make_image_bytes(seed=22)
        candidate_phash = compute_phash(img_bytes)
        assert candidate_phash is not None

        me = _make_user(db, suffix="solo")
        _seed_peer_receipt_with_phash(db, user_id=me.id, phash_hex=candidate_phash)

        my_receipt = _make_receipt(db, user_id=me.id, image_r2_key="img-solo.jpg")
        _run_task(db, my_receipt, img_bytes)

        # Phase 0 did NOT reject : OCR (the stub) was invoked.
        assert run_calls == [my_receipt.id], f"expected OCR to run for same-user re-upload, got {run_calls!r}"

        # No fraud_suspicions row was created.
        suspicions = db.query(FraudSuspicion).filter(FraudSuspicion.receipt_id == my_receipt.id).all()
        assert suspicions == []

        # No 'image_duplicate' rejection scan.
        scans = (
            db.query(Scan)
            .filter(Scan.receipt_id == my_receipt.id)
            .filter(Scan.rejected_reason == "image_duplicate")
            .all()
        )
        assert scans == []

    def test_phash_compute_crash_falls_through_to_ocr(self, db, monkeypatch):
        """If ``compute_phash`` raises, OCR must still proceed
        (fail-safe — anti-fraud never blocks a legitimate scan)."""
        _force_pipeline(monkeypatch)
        run_calls = _stub_pipeline_orchestrator_noop(monkeypatch)

        def _boom(_bytes):
            raise RuntimeError("simulated phash crash")

        monkeypatch.setattr("worker.pipeline.phash.compute_phash", _boom)

        me = _make_user(db, suffix="crash")
        my_receipt = _make_receipt(db, user_id=me.id, image_r2_key="crash.jpg")
        _run_task(db, my_receipt, _make_image_bytes(seed=33))

        # OCR still ran.
        assert run_calls == [my_receipt.id]

        # No phantom phash on the receipt — compute returned nothing
        # usable.
        db.expire_all()
        r = db.get(Receipt, my_receipt.id)
        assert r.image_phash is None

        # No fraud_suspicions row.
        suspicions = db.query(FraudSuspicion).filter(FraudSuspicion.receipt_id == my_receipt.id).all()
        assert suspicions == []

    def test_phash_lookup_crash_falls_through_to_ocr(self, db, monkeypatch):
        """If ``lookup_phash_cross_user`` raises, the wrapper must
        degrade to ``("ok", phash_hex)`` so OCR proceeds AND the
        pHash is still persisted post-pipeline."""
        _force_pipeline(monkeypatch)
        run_calls = _stub_pipeline_orchestrator_noop(monkeypatch)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated lookup crash")

        monkeypatch.setattr("worker.pipeline.phash_lookup.lookup_phash_cross_user", _boom)

        me = _make_user(db, suffix="lookup-crash")
        my_receipt = _make_receipt(db, user_id=me.id, image_r2_key="lookup-crash.jpg")
        img_bytes = _make_image_bytes(seed=44)
        _run_task(db, my_receipt, img_bytes)

        # OCR still ran.
        assert run_calls == [my_receipt.id]

        # Despite the lookup crash we computed the pHash successfully
        # so the post-pipeline persistence step still wrote it.
        from worker.pipeline.phash import compute_phash

        db.expire_all()
        r = db.get(Receipt, my_receipt.id)
        assert r.image_phash == compute_phash(img_bytes)

    def test_successful_pipeline_persists_phash_for_future_matches(self, db, monkeypatch):
        """Happy path : no cross-user match → V3 runs → the candidate
        pHash is UPDATEd on ``receipts.image_phash`` so future scans
        can find this receipt as a peer."""
        _force_pipeline(monkeypatch)
        run_calls = _stub_pipeline_orchestrator_noop(monkeypatch)

        me = _make_user(db, suffix="happy")
        my_receipt = _make_receipt(db, user_id=me.id, image_r2_key="happy.jpg")
        img_bytes = _make_image_bytes(seed=55)
        _run_task(db, my_receipt, img_bytes)

        assert run_calls == [my_receipt.id]

        from worker.pipeline.phash import compute_phash

        expected = compute_phash(img_bytes)
        assert expected is not None

        db.expire_all()
        r = db.get(Receipt, my_receipt.id)
        assert r.image_phash == expected

    def test_phash_check_disabled_via_settings_skips_lookup(self, db, monkeypatch):
        """``enable_phash_check=false`` short-circuits phase 0 entirely
        — even with a colliding peer receipt the current scan goes
        through to OCR. Future flag-rollback safety net."""
        _force_pipeline(monkeypatch)
        run_calls = _stub_pipeline_orchestrator_noop(monkeypatch)

        # Patch settings to flip the flag off. ``load_settings`` is a
        # cached helper — patch on the receipt_task module symbol.
        original_load = __import__("worker.receipt_task", fromlist=["load_settings"]).load_settings
        full = original_load()

        def _patched_load_settings():
            import copy

            s = copy.deepcopy(full)
            s.setdefault("pipeline", {}).setdefault("anti_fraud", {})["enable_phash_check"] = False
            return s

        monkeypatch.setattr("worker.receipt_task.load_settings", _patched_load_settings)

        from worker.pipeline.phash import compute_phash

        img_bytes = _make_image_bytes(seed=66)
        candidate = compute_phash(img_bytes)
        peer = _make_user(db, suffix="peer-flag")
        me = _make_user(db, suffix="me-flag")
        _seed_peer_receipt_with_phash(db, user_id=peer.id, phash_hex=candidate)

        my_receipt = _make_receipt(db, user_id=me.id, image_r2_key="flag.jpg")
        _run_task(db, my_receipt, img_bytes)

        # OCR still ran — phase 0 was skipped.
        assert run_calls == [my_receipt.id]
        suspicions = db.query(FraudSuspicion).filter(FraudSuspicion.receipt_id == my_receipt.id).all()
        assert suspicions == []


class TestPhashLookupPerformance:
    """Performance gate : the cross-user pHash lookup must stay fast
    even with a populated table. The ARCH targets <100ms with the
    partial index ``idx_receipts_image_phash`` in place — we set
    a loose 250ms budget to absorb CI jitter while still catching
    a fundamentally O(N²) regression.
    """

    def test_lookup_under_250ms_with_1000_seeded_phashes(self, db):
        from worker.pipeline.phash_lookup import lookup_phash_cross_user

        peer = _make_user(db, suffix="perf-peer")
        me = _make_user(db, suffix="perf-me")

        # Seed 1000 receipts with random-looking pHashes. We avoid the
        # candidate's neighborhood so we measure the worst case "scan
        # the index and reject everything".
        seeds = []
        import secrets

        for _ in range(1000):
            ph = secrets.token_hex(8)  # 16 hex chars = 64 bits
            seeds.append(
                {
                    "id": uuid.uuid4(),
                    "uid": peer.id,
                    "ph": ph,
                }
            )
        # Bulk INSERT for speed.
        db.execute(
            text("INSERT INTO receipts (id, user_id, purchased_at, image_phash) VALUES (:id, :uid, CURRENT_DATE, :ph)"),
            seeds,
        )
        db.commit()

        # Probe candidate guaranteed not to match (Hamming distance
        # threshold = 0 → only exact matches return ; random 64-bit
        # values almost never collide).
        candidate = "f0f0f0f0f0f0f0f0"
        import time

        t0 = time.perf_counter()
        result = lookup_phash_cross_user(
            db,
            user_id=me.id,
            candidate_phash_hex=candidate,
            max_hamming_distance=0,
            window_days=30,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is None
        assert elapsed_ms < 250, f"pHash cross-user lookup too slow on 1000-row corpus: {elapsed_ms:.1f}ms"
