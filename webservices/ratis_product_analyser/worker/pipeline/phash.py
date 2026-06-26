"""Perceptual hash (pHash) helpers — anti-fraud PR2 (phase 0 of pipeline).

Computes a 64-bit perceptual hash of the receipt image source so we can
detect cross-user image re-submissions BEFORE running OCR (cf
``ARCH_receipt_pipeline.md`` § "Réconciliation tickets — V1" — step 2
of the pipeline flow).

The hash is later persisted on ``receipts.image_phash`` (16-char hex)
and looked up cross-user via :mod:`worker.pipeline.phash_lookup`
with a Hamming-distance threshold (``pipeline.anti_fraud.phash_hamming_threshold``,
default 8) in a sliding window (``phash_window_days``, default 30).

Design notes
------------

* **Algorithm** : ``imagehash.phash`` (DCT-based perceptual hash, default
  hash_size=8 → 64 bits → 16 hex chars). Robust to JPEG re-encoding,
  small crops, brightness / contrast tweaks — the typical receipt
  re-share modifications.
* **Failure mode** : on a corrupted / unsupported image we log + return
  ``None`` so the caller can skip the cross-user check and continue
  the OCR path (fail-safe — anti-fraud must NEVER block a legitimate
  scan because of a hash compute bug).
* **No DB access** : pure function. Lookup lives in ``phash_lookup``.
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, UnidentifiedImageError

try:  # Optional at import time so tests that don't exercise pHash work
    import imagehash
except ImportError:  # pragma: no cover — declared in pyproject deps
    imagehash = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


PHASH_HEX_LEN = 16  # imagehash.phash with hash_size=8 → 64 bits → 16 hex


def compute_phash(image_bytes: bytes) -> str | None:
    """Compute a 64-bit perceptual hash, return its 16-char hex form.

    Args:
        image_bytes: raw JPEG / PNG bytes from R2 (the same payload the
            extract Phase 1 will OCR). Empty / corrupted bytes return
            ``None`` and log a warning — the caller continues OCR.

    Returns:
        16-char lowercase hex string (e.g. ``"f8c4a3e1b2d50907"``)  # pragma: allowlist secret
        or ``None`` if the image cannot be decoded by Pillow.

    Notes:
        * The library returns an :class:`imagehash.ImageHash` whose
          ``__str__`` is already lowercase hex with a deterministic
          width (``hash.size // 4``). We assert the length defensively
          so a future ``hash_size`` change here doesn't silently break
          downstream consumers that store the column as ``VARCHAR(16)``.
        * Pillow's ``Image.open`` is lazy — calling ``.load()`` (or
          ``imagehash`` doing it internally) materialises the pixels
          and is what triggers the decode error on a corrupted file.
    """
    if imagehash is None:  # pragma: no cover — declared dep
        logger.warning("imagehash library not available — skipping pHash compute")
        return None

    if not image_bytes:
        logger.warning("compute_phash: empty image_bytes — returning None")
        return None

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            # imagehash.phash works on grayscale + DCT internally ; passing
            # the raw PIL image is the canonical entry point. hash_size=8
            # (default) → 64-bit hash.
            ph = imagehash.phash(img)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # OSError covers truncated streams ; ValueError covers a few
        # corrupted-PNG codepaths in older Pillow. Anti-fraud is best-
        # effort — never raise upstream.
        logger.warning(
            "compute_phash failed to decode image (%d bytes): %s",
            len(image_bytes),
            exc,
        )
        return None
    except Exception as exc:
        logger.warning(
            "compute_phash unexpected error (%d bytes): %s",
            len(image_bytes),
            exc,
            exc_info=True,
        )
        return None

    hex_value = str(ph)
    if len(hex_value) != PHASH_HEX_LEN:
        # Should never happen with hash_size=8 — guard against a future
        # imagehash upgrade silently widening the hash.
        logger.warning(
            "compute_phash produced unexpected length %d (value=%r) — expected %d ; skipping",
            len(hex_value),
            hex_value,
            PHASH_HEX_LEN,
        )
        return None
    return hex_value
