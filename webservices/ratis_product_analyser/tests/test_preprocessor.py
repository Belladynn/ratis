"""Unit tests for preprocessor passes — added in feat/preprocessing-ocr-v2.

Covers:
  - _deskew (barcode-anchored, replaces Hough — see Intermarché ddc1d82f
    scan 2026-04-27 where Hough rotated by ~20° due to keyboard in frame)
  - pass_binarized (Multi-Otsu 3-class threshold — drops bleed-through —
    feat/multiotsu-binarized 2026-04-28)
"""

from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
from worker.ocr import preprocessor as pp

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_text_image(width: int = 800, height: int = 1200) -> np.ndarray:
    """Create a grayscale image with synthetic horizontal "text" lines."""
    img = np.full((height, width), 240, dtype=np.uint8)  # near-white background
    for y in range(100, height - 100, 60):
        cv2.line(img, (50, y), (width - 50, y), 30, thickness=4)
    return img


def _fake_barcode_symbol(polygon_pts):
    """Mimic the shape of a pyzbar Decoded result for monkeypatch.

    Only the attributes our _deskew touches are populated : .polygon (list
    of objects with .x/.y) and .rect (with .width/.height for sizing).
    """
    points = [SimpleNamespace(x=int(x), y=int(y)) for x, y in polygon_pts]
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    rect = SimpleNamespace(
        width=max(xs) - min(xs),
        height=max(ys) - min(ys),
    )
    return SimpleNamespace(polygon=points, rect=rect)


# ── _deskew (barcode-anchored) ───────────────────────────────────────────────


class TestDeskewBarcodeAnchored:
    def test_returns_unchanged_when_no_barcode(self, monkeypatch):
        """No barcode detected → no rotation (safer than wild Hough guess)."""
        monkeypatch.setattr(
            "worker.ocr.preprocessor.zbar_decode",
            lambda *a, **kw: [],
        )
        img = _make_text_image()
        out = pp._deskew(img)
        assert out is img or np.array_equal(out, img)

    def test_returns_unchanged_when_barcode_already_straight(self, monkeypatch):
        """Barcode already horizontal (angle ~0) → skip rotation."""
        # Wide horizontal rectangle (300 wide × 60 tall) at top of image.
        polygon = [(100, 50), (400, 50), (400, 110), (100, 110)]
        monkeypatch.setattr(
            "worker.ocr.preprocessor.zbar_decode",
            lambda *a, **kw: [_fake_barcode_symbol(polygon)],
        )
        img = _make_text_image()
        out = pp._deskew(img)
        # Should be the input array (early-return path) — same shape, same
        # content (no rotation applied).
        assert out.shape == img.shape
        assert np.array_equal(out, img)

    def test_rotates_when_barcode_skewed(self, monkeypatch):
        """Barcode tilted by ~30° → image gets de-rotated by ~30°."""
        # 4-corner polygon of a barcode tilted 30° clockwise around its
        # center. Easier to build by rotating 4 corners of an axis-aligned
        # rectangle.
        cx, cy = 400, 400
        w, h = 300, 60
        corners = np.array(
            [
                [cx - w / 2, cy - h / 2],
                [cx + w / 2, cy - h / 2],
                [cx + w / 2, cy + h / 2],
                [cx - w / 2, cy + h / 2],
            ]
        )
        theta = np.deg2rad(30.0)
        R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        rotated = (corners - [cx, cy]) @ R.T + [cx, cy]
        polygon = [tuple(p) for p in rotated]

        monkeypatch.setattr(
            "worker.ocr.preprocessor.zbar_decode",
            lambda *a, **kw: [_fake_barcode_symbol(polygon)],
        )
        img = _make_text_image()
        out = pp._deskew(img)
        # Output must be a fresh array (rotation applied) and same shape.
        assert out.shape == img.shape
        assert not np.array_equal(out, img)

    def test_returns_unchanged_when_polygon_malformed(self, monkeypatch):
        """Polygon with !=4 points → can't compute orientation → skip."""
        polygon = [(100, 50), (400, 50), (400, 110)]  # only 3 points
        monkeypatch.setattr(
            "worker.ocr.preprocessor.zbar_decode",
            lambda *a, **kw: [_fake_barcode_symbol(polygon)],
        )
        img = _make_text_image()
        out = pp._deskew(img)
        assert np.array_equal(out, img)

    def test_picks_largest_barcode(self, monkeypatch):
        """When multiple barcodes are detected, the largest should win.

        A small EAN-13 from a side product bleed-through must not override
        the receipt's main barcode. The test passes two barcodes with
        different rect sizes ; the larger one's polygon is straight, the
        smaller one is tilted. Result must be no rotation (largest is
        straight)."""
        big_straight = _fake_barcode_symbol([(100, 50), (500, 50), (500, 110), (100, 110)])
        small_tilted = _fake_barcode_symbol([(10, 10), (40, 30), (35, 50), (5, 30)])
        monkeypatch.setattr(
            "worker.ocr.preprocessor.zbar_decode",
            lambda *a, **kw: [small_tilted, big_straight],
        )
        img = _make_text_image()
        out = pp._deskew(img)
        assert np.array_equal(out, img)


# ── pass_binarized (Multi-Otsu 3-class threshold) ────────────────────────────


class TestPassBinarizedMultiOtsu:
    """Multi-Otsu 3-class binarization — drops bleed-through.

    The receipt histogram is typically trimodal :
      paper (~250) · bleed-through (~150-200) · direct print (~30-80)
    `threshold_multiotsu(classes=3)` finds the 2 thresholds that split
    these classes ; we keep ONLY pixels darker than the lowest threshold
    (= direct print), turning bleed-through to white background.

    Falls back to cv2.adaptiveThreshold when the histogram does not have
    3 distinct classes (ValueError from skimage).
    """

    def test_output_dtype_and_shape(self):
        """Binary output must be uint8 and same H×W as input."""
        img = np.full((400, 400), 240, dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (350, 100), 40, -1)
        out = pp.pass_binarized(img)
        assert out.shape == img.shape
        assert out.dtype == np.uint8
        # binary : every pixel is either 0 or 255
        unique_vals = set(np.unique(out).tolist())
        assert unique_vals.issubset({0, 255})

    def test_accepts_color_input(self):
        """3-channel BGR input is converted to gray before thresholding."""
        bgr = np.full((300, 300, 3), 240, dtype=np.uint8)
        cv2.rectangle(bgr, (40, 40), (260, 80), (30, 30, 30), -1)
        out = pp.pass_binarized(bgr)
        # Output is single-channel (gray-binarized), same H×W
        assert out.shape == bgr.shape[:2]
        assert out.dtype == np.uint8

    def test_bimodal_paper_print_keeps_print_black(self):
        """Realistic ticket-like image with text on paper (no bleed) —
        print pixels stay black, paper stays white. The synthetic image
        has light noise so the histogram is not degenerate (a perfectly
        uniform image is pathological for both Multi-Otsu and
        cv2.adaptiveThreshold)."""
        rng = np.random.default_rng(42)
        h, w = 400, 400
        img = np.full((h, w), 240, dtype=np.uint8)  # paper background
        # Synthetic "text" : 3 horizontal lines (typical ticket lines)
        for y in (60, 80, 100):
            cv2.line(img, (60, y), (340, y), 30, thickness=3)
        # Light noise (~±5) so the histogram has continuous values rather
        # than 2 discrete bins — Multi-Otsu requires >=3 bins after
        # discretization to find 2 thresholds.
        noise = rng.integers(-5, 6, size=img.shape, dtype=np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        out = pp.pass_binarized(img)
        # Center of a print line must be black
        assert out[60, 200] == 0
        # Paper area (no print) must be white
        assert out[300, 200] == 255

    def test_trimodal_drops_bleed(self):
        """Image with 3 classes (paper + bleed + print) — Multi-Otsu must
        keep only the print band as black, bleed and paper become white."""
        h, w = 400, 400
        img = np.full((h, w), 240, dtype=np.uint8)  # paper (light)
        cv2.rectangle(img, (50, 200), (350, 250), 170, -1)  # bleed (mid-gray)
        cv2.rectangle(img, (50, 50), (350, 100), 40, -1)  # print (dark)
        out = pp.pass_binarized(img)
        # Print stays black
        assert out[75, 200] == 0
        # Bleed becomes white (dropped — the main improvement vs adaptive)
        assert out[225, 200] == 255
        # Paper white
        assert out[300, 200] == 255

    def test_unimodal_falls_back_gracefully(self):
        """Near-uniform image — threshold_multiotsu raises ValueError, we
        fall back to cv2.adaptiveThreshold. Must not crash, must return a
        valid binarized image."""
        img = np.full((400, 400), 128, dtype=np.uint8)
        img[50:60, 50:60] = 100  # micro variation (not 3 classes)
        out = pp.pass_binarized(img)
        assert out.shape == img.shape
        assert out.dtype == np.uint8
        unique_vals = set(np.unique(out).tolist())
        assert unique_vals.issubset({0, 255})
