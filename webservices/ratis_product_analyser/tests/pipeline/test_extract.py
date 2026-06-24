"""Phase 1 extract tests (pure, no DB).

Cover :

- ``image_hash`` reproducibility
- audit-log event emission (started / completed)
- per-block / per-barcode events at ``log_level='verbose'`` only
- :class:`RawTicket` immutability (Pydantic frozen)
- monkeypatched paddleocr / pyzbar paths (stubbed without engine load)
- empty OCR result → empty blocks (not an exception)
- (integration) real ``intermarche_courbevoie.jpg`` smoke — runs
  PaddleOCR + pyzbar end-to-end, marked ``integration`` so CI nightly
  / opt-in only

Cf. ``ARCH_receipt_pipeline.md`` § Phase 1 Extract et § Verbosité.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from worker.pipeline import extract
from worker.pipeline.extract import (
    AuditLogger,
    ExtractError,
    extract_raw_ticket,
)
from worker.pipeline.types import (
    RawBarcode,
    RawBlock,
    compute_barcode_hash,
    compute_block_hash,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_IMAGE_PATH = FIXTURES_DIR / "intermarche_courbevoie.jpg"

# Tiny synthetic image bytes — not a valid image (intentionally) ; tests
# that monkeypatch _run_paddleocr / _run_pyzbar bypass image decoding,
# so the bytes content does not matter for hashing reproducibility.
SAMPLE_BYTES = b"\x89PNG_fake_bytes_for_hash_tests_only"
CAPTURED_AT = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


def _make_collecting_logger() -> tuple[AuditLogger, list[dict[str, Any]]]:
    """Return (callback, events list). The callback appends each event
    so tests can assert what was emitted and at which level."""
    events: list[dict[str, Any]] = []

    def _log(*, phase: str, level: str, event: str, payload: dict | None = None) -> None:
        events.append({"phase": phase, "level": level, "event": event, "payload": payload})

    return _log, events


def _stub_block(text: str = "BANANE 1.50") -> RawBlock:
    bbox = (10.0, 20.0, 100.0, 30.0)
    return RawBlock(
        text=text,
        bbox=bbox,
        confidence=0.95,
        content_hash=compute_block_hash(text, bbox, 0.95),
    )


def _stub_barcode(value: str = "3245678901234") -> RawBarcode:
    bbox = (5.0, 5.0, 200.0, 60.0)
    return RawBarcode(
        value=value,
        format="EAN13",
        bbox=bbox,
        content_hash=compute_barcode_hash(value, "EAN13", bbox),
    )


# ---------------------------------------------------------------------------
# image_hash reproducibility
# ---------------------------------------------------------------------------


def test_extract_image_hash_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls with the same bytes must produce the same image_hash."""
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    t1 = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    t2 = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    assert t1.image_hash == t2.image_hash
    assert len(t1.image_hash) == 64  # sha256 hex


def test_extract_image_hash_differs_on_byte_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct bytes ⇒ distinct image_hash."""
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    t1 = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    t2 = extract_raw_ticket(SAMPLE_BYTES + b"x", captured_at=CAPTURED_AT)
    assert t1.image_hash != t2.image_hash


# ---------------------------------------------------------------------------
# Audit log event emission
# ---------------------------------------------------------------------------


def test_extract_emits_audit_started_and_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [_stub_block()])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [_stub_barcode()])
    audit, events = _make_collecting_logger()

    extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT, audit_logger=audit)

    names = [e["event"] for e in events]
    assert "extract_started" in names
    assert "extract_completed" in names
    started = next(e for e in events if e["event"] == "extract_started")
    assert started["phase"] == "extract"
    assert started["level"] == "normal"
    assert started["payload"]["image_size_bytes"] == len(SAMPLE_BYTES)
    completed = next(e for e in events if e["event"] == "extract_completed")
    assert completed["payload"]["block_count"] == 1
    assert completed["payload"]["barcode_count"] == 1


def test_extract_audit_normal_no_per_block_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """At log_level='normal', no per-block 'ocr_block' events are emitted."""

    def fake_paddle(*_a: Any, audit_logger: AuditLogger, log_level: str, **_kw: Any) -> list[RawBlock]:
        # Mirror real helper behavior: 'ocr_block' only at verbose.
        return [_stub_block(f"line {i}") for i in range(3)]

    monkeypatch.setattr(extract, "_run_paddleocr", fake_paddle)
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])
    audit, events = _make_collecting_logger()

    extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT, audit_logger=audit, log_level="normal")

    assert not any(e["event"] == "ocr_block" for e in events)


def test_extract_audit_verbose_emits_per_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """At log_level='verbose', one 'ocr_block' event per block is emitted."""
    blocks = [_stub_block("a"), _stub_block("b"), _stub_block("c")]

    # Use the real _run_paddleocr ? No — that needs PaddleOCR. Instead we
    # call the real helper-functions chain by stubbing the legacy engine.
    # Here we go simpler : monkeypatch _run_paddleocr to a thin variant
    # that re-emits the verbose events (mirrors production behavior).
    def fake_paddle(
        _bytes: bytes,
        *,
        audit_logger: AuditLogger,
        log_level: str,
    ) -> list[RawBlock]:
        if log_level == "verbose":
            for b in blocks:
                audit_logger(
                    phase="extract",
                    level="verbose",
                    event="ocr_block",
                    payload={"block_id": str(b.id), "text": b.text, "confidence": b.confidence},
                )
        return blocks

    monkeypatch.setattr(extract, "_run_paddleocr", fake_paddle)
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    audit, events = _make_collecting_logger()
    extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT, audit_logger=audit, log_level="verbose")
    per_block = [e for e in events if e["event"] == "ocr_block"]
    assert len(per_block) == 3
    assert all(e["level"] == "verbose" for e in per_block)


# ---------------------------------------------------------------------------
# Frozen RawTicket — Pydantic immutability
# ---------------------------------------------------------------------------


def test_extract_returns_frozen_raw_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [_stub_block()])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    ticket = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)

    with pytest.raises(ValidationError):
        ticket.blocks = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ticket.image_hash = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Engine wiring — paddleocr & pyzbar reach the ticket output
# ---------------------------------------------------------------------------


def test_extract_paddleocr_results_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever _run_paddleocr returns ends up in ticket.blocks."""
    expected = _stub_block("HELLO")
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [expected])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    ticket = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    assert ticket.blocks == (expected,)


def test_extract_pyzbar_results_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever _run_pyzbar returns ends up in ticket.barcodes."""
    expected = _stub_barcode("1234567890123")
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [expected])

    ticket = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    assert ticket.barcodes == (expected,)


def test_extract_no_paddleocr_results_returns_empty_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty OCR result is legal — does NOT raise."""
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    ticket = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    assert ticket.blocks == ()
    assert ticket.barcodes == ()


def test_extract_uses_provided_receipt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """If receipt_id is passed, the ticket carries it (no auto-uuid)."""
    from uuid import UUID, uuid4

    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    expected = uuid4()
    ticket = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT, receipt_id=expected)
    assert ticket.receipt_id == expected
    assert isinstance(ticket.receipt_id, UUID)


def test_extract_default_receipt_id_is_generated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "_run_paddleocr", lambda *a, **kw: [])
    monkeypatch.setattr(extract, "_run_pyzbar", lambda *a, **kw: [])

    t1 = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    t2 = extract_raw_ticket(SAMPLE_BYTES, captured_at=CAPTURED_AT)
    assert t1.receipt_id != t2.receipt_id


# ---------------------------------------------------------------------------
# Helpers internal — _four_corners_to_xywh and _decode_image
# ---------------------------------------------------------------------------


def test_four_corners_to_xywh_axis_aligned() -> None:
    """An axis-aligned 4-corner polygon yields the expected xywh."""
    bbox = ((10.0, 20.0), (110.0, 20.0), (110.0, 70.0), (10.0, 70.0))
    assert extract._four_corners_to_xywh(bbox) == (10.0, 20.0, 100.0, 50.0)


def test_four_corners_to_xywh_rotated() -> None:
    """A rotated bbox is projected onto its axis-aligned bounding rect."""
    bbox = ((10.0, 20.0), (110.0, 30.0), (108.0, 80.0), (8.0, 70.0))
    x, y, w, h = extract._four_corners_to_xywh(bbox)
    assert (x, y) == (8.0, 20.0)
    assert (w, h) == (110.0 - 8.0, 80.0 - 20.0)


def test_decode_image_raises_on_invalid_bytes() -> None:
    with pytest.raises(ExtractError):
        extract._decode_image(b"not-an-image")


def test_decode_image_rejects_decompression_bomb() -> None:
    """An image whose decoded surface exceeds ocr.max_image_pixels is
    rejected before cv2.imdecode — the file-size cap does not bound it."""
    import io as _io

    from PIL import Image as _Image

    buf = _io.BytesIO()
    # 7000 * 7000 = 49M px > 40M cap, yet only tens of KB encoded.
    _Image.new("RGB", (7000, 7000), color=(0, 0, 0)).save(buf, format="PNG")
    with pytest.raises(ExtractError):
        extract._decode_image(buf.getvalue())


# ---------------------------------------------------------------------------
# pyzbar runner — synthetic decode result
# ---------------------------------------------------------------------------


def test_run_pyzbar_synthetic_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    """With pyzbar.decode monkeypatched, _run_pyzbar produces the expected RawBarcode."""
    # Stub _decode_image — pyzbar isn't actually invoked on real bytes here.
    monkeypatch.setattr(extract, "_decode_image", lambda _b: object())

    fake_decoded = [
        SimpleNamespace(
            data=b"3245678901234",
            type="EAN13",
            rect=SimpleNamespace(left=5, top=10, width=200, height=60),
        )
    ]

    import sys
    import types

    # Build a fake pyzbar module so the lazy `from pyzbar import pyzbar`
    # inside _run_pyzbar resolves without needing libzbar at runtime.
    fake_pyzbar_inner = types.SimpleNamespace(decode=lambda _img: fake_decoded)
    fake_pyzbar_pkg = types.ModuleType("pyzbar")
    fake_pyzbar_pkg.pyzbar = fake_pyzbar_inner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyzbar", fake_pyzbar_pkg)
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", fake_pyzbar_inner)

    audit, _events = _make_collecting_logger()
    barcodes = extract._run_pyzbar(SAMPLE_BYTES, audit_logger=audit, log_level="normal")
    assert len(barcodes) == 1
    b = barcodes[0]
    assert b.value == "3245678901234"
    assert b.format == "EAN13"
    assert b.bbox == (5.0, 10.0, 200.0, 60.0)
    # Hash is reproducible
    assert b.content_hash == compute_barcode_hash("3245678901234", "EAN13", b.bbox)


def test_run_pyzbar_no_codes_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "_decode_image", lambda _b: object())

    import sys
    import types

    fake_inner = types.SimpleNamespace(decode=lambda _img: [])
    fake_pkg = types.ModuleType("pyzbar")
    fake_pkg.pyzbar = fake_inner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyzbar", fake_pkg)
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", fake_inner)

    audit, _events = _make_collecting_logger()
    assert extract._run_pyzbar(SAMPLE_BYTES, audit_logger=audit, log_level="normal") == []


# ---------------------------------------------------------------------------
# ocr_engine_version — paddleocr available or fallback
# ---------------------------------------------------------------------------


def test_ocr_engine_version_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """When paddleocr is importable, version follows 'paddleocr-<v>-fr'."""
    import sys
    import types

    fake_module = types.ModuleType("paddleocr")
    fake_module.__version__ = "2.9.1"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "paddleocr", fake_module)
    assert extract._get_ocr_engine_version() == "paddleocr-2.9.1-fr"


# ---------------------------------------------------------------------------
# Integration smoke — real PaddleOCR + pyzbar on a real receipt
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_extract_real_image_smoke() -> None:
    """End-to-end smoke on the real intermarche_courbevoie.jpg fixture.

    Marked ``integration`` — runs only when ``-m integration`` is passed
    (or under nightly CI). Default pytest run skips it ; this keeps the
    fast suite under 60 s while still exercising the real engine when
    explicitly requested.

    Skipped if paddlepaddle/paddleocr can't be imported on the current
    platform (e.g., Linux aarch64 has no paddlepaddle wheel — cf marker
    in webservices/ratis_product_analyser/pyproject.toml).
    """
    pytest.importorskip(
        "paddlepaddle",
        reason="paddlepaddle wheel not available on this platform (e.g., Linux aarch64)",
    )
    pytest.importorskip("paddleocr", reason="paddleocr unavailable")

    if not SAMPLE_IMAGE_PATH.exists():
        pytest.skip(f"fixture {SAMPLE_IMAGE_PATH.name} not present")

    image_bytes = SAMPLE_IMAGE_PATH.read_bytes()
    audit, events = _make_collecting_logger()
    ticket = extract_raw_ticket(
        image_bytes,
        captured_at=CAPTURED_AT,
        audit_logger=audit,
        log_level="normal",
    )
    # OCR is non-deterministic in *content* but always returns >=1 block on
    # a real receipt photo. Any zero-block result indicates an engine
    # failure that we want to hear about loudly.
    assert len(ticket.blocks) >= 1, "real receipt should yield at least one OCR block"
    assert len(ticket.image_hash) == 64
    # Re-running on the same bytes yields the same image_hash.
    again = extract_raw_ticket(
        image_bytes,
        captured_at=CAPTURED_AT,
        audit_logger=lambda **_: None,
        log_level="normal",
    )
    assert again.image_hash == ticket.image_hash
    # Audit emitted both bookend events.
    names = [e["event"] for e in events]
    assert "extract_started" in names
    assert "extract_completed" in names
