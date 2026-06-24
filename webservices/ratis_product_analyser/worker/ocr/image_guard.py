"""Shared image-decoding guard against decompression bombs.

The upload-validation layer (:func:`ratis_core.uploads.validate_image_upload`)
caps the *encoded* file size, but a small encoded file can still decode to a
huge raster — a classic decompression bomb that can exhaust worker memory
during ``cv2.imdecode``.

This module exposes :func:`assert_image_dimensions_ok`, called by every
decode path (receipt / label / pipeline extract) BEFORE ``cv2.imdecode``.
It reads only the image *header* via Pillow (``Image.open(...).size`` does
not call ``.load()``, so no full raster is materialised) and rejects images
whose pixel count exceeds the ``ocr.max_image_pixels`` settings cap.
"""

from __future__ import annotations

import io

from ratis_core.settings import load_settings

from worker.ocr.exceptions import InvalidImageError


def _max_image_pixels() -> int:
    """Pixel-count cap from ratis_settings.json (R19 — never hardcode)."""
    return load_settings()["ocr"]["max_image_pixels"]


def assert_image_dimensions_ok(image_bytes: bytes) -> None:
    """Reject decompression bombs before an unbounded ``cv2.imdecode``.

    Reads the image header only (Pillow ``Image.open`` is lazy — ``.size``
    is available without ``.load()``), so the check itself never decodes the
    full raster.

    Raises:
        InvalidImageError: ``image_dimensions_exceeded`` when the decoded
            surface (``width * height``) would exceed ``ocr.max_image_pixels``.
            Also ``corrupted_or_spoofed_image`` when Pillow cannot even read
            the header — the bytes are not a usable image.
    """
    from PIL import Image  # lazy — heavy native lib, do not import at module load

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
    except Exception as exc:
        raise InvalidImageError("corrupted_or_spoofed_image") from exc

    if width * height > _max_image_pixels():
        raise InvalidImageError("image_dimensions_exceeded")
