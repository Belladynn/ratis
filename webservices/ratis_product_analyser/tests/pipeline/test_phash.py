"""Unit tests for :mod:`worker.pipeline.phash` (anti-fraud PR2).

Cover :

- :func:`compute_phash` returns a 16-char lowercase hex string on a
  valid image
- identical images produce identical hashes (reproducibility)
- visually similar images produce hashes with low Hamming distance
- visually different images produce hashes with high Hamming distance
- corrupted / empty / non-image bytes return ``None`` (fail-safe)

Cf. ``ARCH_receipt_pipeline.md`` § "Réconciliation tickets — V1" step 2.
"""

from __future__ import annotations

from io import BytesIO

import imagehash
from PIL import Image
from worker.pipeline.phash import PHASH_HEX_LEN, compute_phash


def _png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gradient_image(seed: int = 0, size: int = 128) -> Image.Image:
    """Generate a deterministic gradient image — distinctive enough that
    two different seeds yield well-separated pHashes (>20 bits apart)."""
    img = Image.new("RGB", (size, size))
    pixels = img.load()
    for x in range(size):
        for y in range(size):
            r = (x * 2 + seed * 17) % 256
            g = (y * 2 + seed * 31) % 256
            b = ((x + y) + seed * 47) % 256
            pixels[x, y] = (r, g, b)
    return img


# ────────────────────────────────────────────────────────────────────
# Happy-path : valid input → 16-char hex
# ────────────────────────────────────────────────────────────────────


def test_compute_phash_returns_16_char_hex_on_valid_image():
    img_bytes = _png_bytes(_gradient_image(seed=1))
    h = compute_phash(img_bytes)
    assert h is not None
    assert isinstance(h, str)
    assert len(h) == PHASH_HEX_LEN == 16
    assert all(c in "0123456789abcdef" for c in h), f"non-hex output: {h!r}"


def test_compute_phash_is_deterministic_for_same_bytes():
    img_bytes = _png_bytes(_gradient_image(seed=2))
    h1 = compute_phash(img_bytes)
    h2 = compute_phash(img_bytes)
    assert h1 is not None
    assert h2 is not None
    assert h1 == h2


def test_compute_phash_visually_similar_images_are_close():
    """A 1-pixel rgb tweak should leave Hamming distance very low (≤ 2)."""
    base = _gradient_image(seed=3, size=128)
    near = base.copy()
    # Perturb a single pixel by 1 unit in each channel — well below the
    # perceptual threshold.
    px = near.load()
    r, g, b = px[0, 0]
    px[0, 0] = (min(255, r + 1), min(255, g + 1), min(255, b + 1))

    h_base = compute_phash(_png_bytes(base))
    h_near = compute_phash(_png_bytes(near))
    assert h_base is not None
    assert h_near is not None
    d = imagehash.hex_to_hash(h_base) - imagehash.hex_to_hash(h_near)
    assert d <= 2, f"Expected near-identical images to share pHash (d ≤ 2), got d={d} (base={h_base}, near={h_near})"


def test_compute_phash_visually_different_images_are_far():
    """Two unrelated gradients should be well-separated (>10 bits)."""
    a = _png_bytes(_gradient_image(seed=10))
    b = _png_bytes(_gradient_image(seed=200))
    h_a = compute_phash(a)
    h_b = compute_phash(b)
    assert h_a is not None
    assert h_b is not None
    d = imagehash.hex_to_hash(h_a) - imagehash.hex_to_hash(h_b)
    assert d > 10, f"Expected dissimilar images to be far apart (d > 10), got d={d}"


# ────────────────────────────────────────────────────────────────────
# Fail-safe : corrupted / empty inputs → None (NO raise)
# ────────────────────────────────────────────────────────────────────


def test_compute_phash_returns_none_on_empty_bytes():
    assert compute_phash(b"") is None


def test_compute_phash_returns_none_on_garbage_bytes(caplog):
    """A clearly non-image payload must not raise — anti-fraud is best-
    effort and OCR must continue if pHash can't be computed."""
    with caplog.at_level("WARNING", logger="worker.pipeline.phash"):
        result = compute_phash(b"not an image at all" * 10)
    assert result is None
    assert any("compute_phash failed to decode" in rec.message for rec in caplog.records)


def test_compute_phash_returns_none_on_truncated_png():
    """A truncated PNG (valid magic bytes but cut short) must return
    None rather than propagate Pillow's OSError."""
    full = _png_bytes(_gradient_image(seed=5))
    truncated = full[:32]  # PNG signature + a sliver of IHDR
    assert compute_phash(truncated) is None
