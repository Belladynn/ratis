"""Phase 1 — Extract.

Transforme une image (bytes) en :class:`RawTicket` Pydantic frozen :

- PaddleOCR pour le texte (:class:`RawBlock` ``[]``)
- pyzbar pour les codes-barres (:class:`RawBarcode` ``[]``)
- ``content_hash`` (par bloc / par barcode) + ``image_hash``
  (sha256 des bytes source) pour la traçabilité de bout en bout
  (cf. ``ARCH_receipt_pipeline.md`` § Traçabilité).

Pure fonctionnel : aucune I/O DB ici. L'écriture des events
``pipeline_audit_log`` est faite via le callback ``audit_logger``
injecté ; Phase 4 (persist) wirera ce callback vers la DB.

Anti-patterns (cf. ARCH § Anti-patterns interdits) :

- Aucun bloc OCR n'est jamais drop silencieusement à cette phase.
  Si PaddleOCR plante, on lève :class:`ExtractError` — pas de retour
  d'un :class:`RawTicket` partiel.
- Idem pour pyzbar : si le décodeur lève, l'erreur remonte. Une absence
  de barcode (rien décodé) reste valide → ``barcodes=()``.

Lazy imports — :mod:`paddleocr` et :mod:`pyzbar` sont importés
**dans** les helpers privés. Le cold-start PaddleOCR est de 5-15 s,
on ne le veut pas au boot du module (KP : R-mantra
``ocr=lazy-import``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from worker.pipeline.types import (
    RawBarcode,
    RawBlock,
    RawTicket,
    compute_barcode_hash,
    compute_block_hash,
    compute_image_hash,
)

logger = logging.getLogger(__name__)


class AuditLogger(Protocol):
    """Callback shape for emitting ``pipeline_audit_log`` events.

    Implementations :

    - Phase 4 wirera l'écriture vers la table DB ``pipeline_audit_log``.
    - Tests utilisent un stub list-based (collecte les events dans une liste)
      pour vérifier la traçabilité sans toucher la DB.
    - :func:`_noop_audit` ignore tout — défaut sécurisé pour appels
      "fire-and-forget" qui n'ont pas besoin du log.
    """

    def __call__(
        self,
        *,
        phase: str,
        level: str,
        event: str,
        payload: dict | None = None,
    ) -> None: ...


def _noop_audit(
    *,
    phase: str,
    level: str,
    event: str,
    payload: dict | None = None,
) -> None:
    """Default audit logger : drops every event silently."""
    return None


class ExtractError(Exception):
    """Raised when Phase 1 fails on an unrecoverable input.

    Examples : corrupted image, unsupported format, PaddleOCR engine
    failure that cannot be retried at this layer.

    Per ARCH § Anti-patterns, we never swallow such errors — a silent
    drop would leave a ``receipt`` with no parsed ticket and no audit
    trail explaining why.
    """


def extract_raw_ticket(
    image_bytes: bytes,
    *,
    captured_at: datetime,
    receipt_id: UUID | None = None,
    audit_logger: AuditLogger = _noop_audit,
    log_level: str = "normal",
) -> RawTicket:
    """Run Phase 1 on ``image_bytes``. Returns a frozen :class:`RawTicket`.

    Args:
        image_bytes: l'image source (raw bytes JPEG/PNG).
        captured_at: timestamp de capture (fourni par l'appelant —
            typiquement quand l'utilisateur a pris la photo). Pas
            inféré ici — Phase 1 ne devine rien sur l'image.
        receipt_id: si déjà connu (Phase 4 lie :class:`ParsedTicket` à
            un ``Receipt`` créé en amont). Sinon généré.
        audit_logger: callback pour émettre les events audit. Default
            no-op.
        log_level: ``"verbose"`` / ``"normal"`` / ``"production"``. Voir
            ARCH § Verbosité ``log_level``. Le filtrage par niveau est
            fait par l'implémentation du callback ; ce module se
            contente d'étiqueter chaque event avec son niveau de
            détail.

    Raises:
        ExtractError: si PaddleOCR ou pyzbar plante de manière
            irrécupérable. On NE swallow JAMAIS — un drop silencieux
            est interdit (cf. ARCH § Anti-patterns).
    """
    receipt_id = receipt_id or uuid4()
    image_hash = compute_image_hash(image_bytes)

    audit_logger(
        phase="extract",
        level="normal",
        event="extract_started",
        payload={
            "receipt_id": str(receipt_id),
            "image_hash": image_hash,
            "image_size_bytes": len(image_bytes),
            "log_level": log_level,
        },
    )

    blocks = _run_paddleocr(
        image_bytes,
        audit_logger=audit_logger,
        log_level=log_level,
    )
    barcodes = _run_pyzbar(
        image_bytes,
        audit_logger=audit_logger,
        log_level=log_level,
    )

    ticket = RawTicket(
        receipt_id=receipt_id,
        blocks=tuple(blocks),
        barcodes=tuple(barcodes),
        image_hash=image_hash,
        ocr_engine_version=_get_ocr_engine_version(),
        captured_at=captured_at,
    )

    audit_logger(
        phase="extract",
        level="normal",
        event="extract_completed",
        payload={
            "receipt_id": str(receipt_id),
            "block_count": len(blocks),
            "barcode_count": len(barcodes),
        },
    )

    return ticket


# Helpers privés —————————————————————————————————————————————————————————


def _decode_image(image_bytes: bytes):
    """Decode raw image bytes into a numpy BGR array via OpenCV.

    OpenCV (``opencv-python-headless``) is already a hard dependency
    of the service — see ``pyproject.toml``. PaddleOCR and pyzbar both
    accept numpy arrays directly, so we share one decode pass between
    the two engines instead of decoding twice.
    """
    import cv2  # lazy — heavy native lib, do not import at module load
    import numpy as np

    from worker.ocr.exceptions import InvalidImageError
    from worker.ocr.image_guard import assert_image_dimensions_ok

    # Decompression-bomb guard — reject oversized rasters before the
    # unbounded cv2.imdecode (the file-size cap does not bound dimensions).
    # The shared guard raises InvalidImageError ; remap to ExtractError so
    # this module keeps its single-exception contract (cf. module docstring).
    try:
        assert_image_dimensions_ok(image_bytes)
    except InvalidImageError as exc:
        raise ExtractError(f"Image rejected before decode — {exc}") from exc

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ExtractError("Failed to decode image bytes — corrupted or unsupported format")
    return img


def _run_paddleocr(
    image_bytes: bytes,
    *,
    audit_logger: AuditLogger,
    log_level: str,
) -> list[RawBlock]:
    """Lazy-import + run PaddleOCR. Returns a list of :class:`RawBlock`.

    Reuses the legacy :class:`worker.ocr.ocr_engine.PaddleOcrEngine`
    wrapper (``recognize_rich``) which already exposes (text, confidence,
    4-corner bbox). We adapt its 4-corner bbox to the
    ``(x, y, w, h)`` shape mandated by :class:`RawBlock`.
    """
    from worker.ocr.ocr_engine import PaddleOcrEngine  # lazy

    image = _decode_image(image_bytes)
    try:
        engine = PaddleOcrEngine()
        rich_blocks = engine.recognize_rich(image)
    except RuntimeError as exc:
        # paddleocr import failure (engine wrapper raises RuntimeError on
        # missing lib). Any other exception is genuinely unexpected and we
        # let it bubble up unchanged.
        raise ExtractError(f"PaddleOCR engine unavailable: {exc}") from exc

    blocks: list[RawBlock] = []
    for rb in rich_blocks:
        bbox_xywh = _four_corners_to_xywh(rb.bbox)
        blocks.append(
            RawBlock(
                text=rb.text,
                bbox=bbox_xywh,
                confidence=float(rb.confidence),
                content_hash=compute_block_hash(rb.text, bbox_xywh, float(rb.confidence)),
            )
        )
    if log_level == "verbose":
        for b in blocks:
            audit_logger(
                phase="extract",
                level="verbose",
                event="ocr_block",
                payload={
                    "block_id": str(b.id),
                    "text": b.text,
                    "confidence": b.confidence,
                    "content_hash": b.content_hash,
                },
            )
    return blocks


def _run_pyzbar(
    image_bytes: bytes,
    *,
    audit_logger: AuditLogger,
    log_level: str,
) -> list[RawBarcode]:
    """Lazy-import + run pyzbar. Returns a list of :class:`RawBarcode`.

    pyzbar accepts both PIL images and numpy arrays. We feed numpy
    (already decoded by :func:`_decode_image`) to keep the dependency
    surface small (no Pillow at runtime).
    """
    from pyzbar import pyzbar  # type: ignore[import-untyped]  # lazy

    image = _decode_image(image_bytes)
    try:
        decoded = pyzbar.decode(image)
    except Exception as exc:
        # pyzbar wraps libzbar — runtime errors here are real, surface them.
        raise ExtractError(f"pyzbar decoding failed: {exc}") from exc

    barcodes: list[RawBarcode] = []
    for d in decoded:
        value = d.data.decode("utf-8", errors="replace")
        fmt = d.type  # 'EAN13' / 'CODE128' / 'QRCODE' / ...
        # pyzbar returns Rect(left, top, width, height) — already xywh.
        bbox = (
            float(d.rect.left),
            float(d.rect.top),
            float(d.rect.width),
            float(d.rect.height),
        )
        barcodes.append(
            RawBarcode(
                value=value,
                format=fmt,
                bbox=bbox,
                content_hash=compute_barcode_hash(value, fmt, bbox),
            )
        )
    if log_level == "verbose":
        for b in barcodes:
            audit_logger(
                phase="extract",
                level="verbose",
                event="barcode_decoded",
                payload={"value": b.value, "format": b.format, "content_hash": b.content_hash},
            )
    return barcodes


def _four_corners_to_xywh(
    bbox: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]],
) -> tuple[float, float, float, float]:
    """Convert a 4-corner polygon (PaddleOCR shape) into ``(x, y, w, h)``.

    PaddleOCR returns the bbox as the four corners of the (possibly
    rotated) text box. We project it onto an axis-aligned rectangle :
    ``x``, ``y`` are the top-left corner ; ``w``, ``h`` the extent.
    Matches the contract documented on :class:`RawBlock` (cf.
    ``types.py`` line 132 — ``(x, y, w, h) — pixel coordinates``).
    """
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    x = min(xs)
    y = min(ys)
    w = max(xs) - x
    h = max(ys) - y
    return (float(x), float(y), float(w), float(h))


def _get_ocr_engine_version() -> str:
    """Return the PaddleOCR version (or a fallback marker).

    Used for :attr:`RawTicket.ocr_engine_version` traceability — bumped
    automatically on engine upgrade since we read ``__version__`` at
    runtime.
    """
    try:
        import paddleocr  # type: ignore[import-untyped]  # lazy

        version = getattr(paddleocr, "__version__", "unknown")
        return f"paddleocr-{version}-fr"
    except Exception:
        return "paddleocr-unknown-fr"
