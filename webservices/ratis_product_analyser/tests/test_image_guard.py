"""Tests for the shared decompression-bomb guard (worker.ocr.image_guard)."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from worker.ocr.exceptions import InvalidImageError
from worker.ocr.image_guard import assert_image_dimensions_ok


def _png_bytes(width: int, height: int) -> bytes:
    """A solid-colour PNG of the given dimensions — small encoded size,
    large decoded surface (the decompression-bomb shape)."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def test_small_image_passes():
    assert assert_image_dimensions_ok(_png_bytes(100, 100)) is None


def test_image_at_cap_passes():
    # 6000 * 6000 = 36M px — under the 40M cap.
    assert assert_image_dimensions_ok(_png_bytes(6000, 6000)) is None


def test_oversized_image_rejected():
    """A small PNG that decodes to > 40M px must be rejected before decode."""
    # 7000 * 7000 = 49M px — over the 40M cap, yet only tens of KB encoded.
    with pytest.raises(InvalidImageError) as exc:
        assert_image_dimensions_ok(_png_bytes(7000, 7000))
    assert str(exc.value) == "image_dimensions_exceeded"


def test_corrupted_bytes_rejected():
    with pytest.raises(InvalidImageError) as exc:
        assert_image_dimensions_ok(b"not an image at all")
    assert str(exc.value) == "corrupted_or_spoofed_image"
