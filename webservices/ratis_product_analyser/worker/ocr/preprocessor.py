from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from pyzbar.pyzbar import ZBarSymbol
from pyzbar.pyzbar import decode as zbar_decode
from skimage.filters import threshold_multiotsu

# Laplacian variance — below this → blurry. Tuned twice on 2026-04-26
# alpha : photos visually readable scored 14-26 even with steady hand
# under good light. The threshold is now permissive (10) — downstream
# OCR pipeline filters out items it can't parse confidently anyway, so
# rejecting at this stage just denied the user an OCR attempt for no
# benefit. Real users will not produce studio-quality photos.
# Cf AF-10 in docs/audits/ALPHA_FEEDBACK.md.
_BLUR_THRESHOLD = 10.0
_BRIGHT_MAX = 230.0  # mean pixel value above this → overexposed
_BRIGHT_MIN = 30.0  # mean pixel value below this → underexposed


@dataclass
class QualityMetrics:
    blur_score: float
    brightness: float
    is_blurry: bool
    is_overexposed: bool
    is_underexposed: bool


def assess_quality(image: np.ndarray) -> QualityMetrics:
    """Compute quality metrics for a grayscale or BGR image."""
    gray = _to_gray(image)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    return QualityMetrics(
        blur_score=blur_score,
        brightness=brightness,
        is_blurry=blur_score < _BLUR_THRESHOLD,
        is_overexposed=brightness > _BRIGHT_MAX,
        is_underexposed=brightness < _BRIGHT_MIN,
    )


def pass_corrected(image: np.ndarray) -> np.ndarray:
    """
    Deskew + brightness normalisation — baseline quality corrections.
    """
    gray = _to_gray(image)
    deskewed = _deskew(gray)
    corrected = _correct_brightness(deskewed)
    return corrected


def pass_clahe(image: np.ndarray) -> np.ndarray:
    """
    CLAHE contrast enhancement — improves readability on low-contrast images.
    """
    gray = _to_gray(image)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return enhanced


def pass_binarized(image: np.ndarray) -> np.ndarray:
    """Multi-Otsu 3-class binarization with adaptive fallback.

    Receipts often have a trimodal intensity histogram :
      - paper background (~250, lightest)
      - bleed-through (~150-200, mid-gray ghost of the back-side print)
      - direct print (~30-80, dark ink)

    Multi-Otsu finds the 2 optimal thresholds separating these 3 classes,
    so we can extract ONLY the direct print (pixels < lowest threshold)
    and drop the bleed entirely — turning bleed pixels white instead of
    letting `cv2.adaptiveThreshold` keep them as noise text.

    Falls back to `cv2.adaptiveThreshold` (the previous algorithm) when
    the histogram is not trimodal — e.g. uniform fill, or only paper +
    print without bleed. `threshold_multiotsu` raises ValueError in that
    case, which we catch.

    Cf. Monoprix scan 2026-04-28 with visible mirror bleed-through where
    the previous adaptive threshold included both bleed and direct text.
    """
    gray = _to_gray(image)
    try:
        # Two thresholds split the histogram into 3 classes :
        #   [0, t0]   = direct print (darkest) — keep as black
        #   (t0, t1]  = bleed-through (mid-gray) — drop to white
        #   (t1, 255] = paper background (lightest) — drop to white
        # `<=` (not `<`) : threshold_multiotsu returns a representative
        # value of the darkest class itself (e.g. exactly 40 on a
        # synthetic histogram where the print pixel value IS 40), so the
        # boundary must include equality to keep that class as black.
        thresholds = threshold_multiotsu(gray, classes=3)
        binarized = np.where(gray <= thresholds[0], 0, 255).astype(np.uint8)
        return binarized
    except ValueError:
        # Histogram has fewer than 3 distinct classes (e.g. bimodal
        # paper+print, or near-uniform). Fall back to the previous
        # adaptive threshold which handles bimodal cleanly.
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=11,
            C=2,
        )


def pass_inverted(image: np.ndarray) -> np.ndarray:
    """
    Inverted contrast + adaptive thresholding — fallback when the 3 main passes diverge.
    """
    gray = _to_gray(image)
    inverted = cv2.bitwise_not(gray)
    binarized = cv2.adaptiveThreshold(
        inverted,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=3,
    )
    return binarized


# ── internal helpers ──────────────────────────────────────────────────────────


def _to_gray(image: np.ndarray) -> np.ndarray:
    if len(image.shape) == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Deskew the receipt using its barcode bounding-box as orientation anchor.

    The receipt's barcode is the most reliable orientation signal :
      - always on the receipt (no false positives from clutter — keyboard,
        table, other objects in the photo frame)
      - always orthogonal to the text direction (printed on the same axis)
      - detected by pyzbar with a precise 4-corner polygon

    Falls back to NO rotation when no barcode is detected, instead of
    guessing via Hough lines on the whole image (the legacy approach
    rotated by ~20° on the Intermarché ddc1d82f scan 2026-04-27 because
    the median of all detected lines was polluted by a keyboard in the
    background — see KP candidate "Hough deskew sur image complète").
    """
    symbols = zbar_decode(
        gray,
        symbols=[
            ZBarSymbol.CODE128,
            ZBarSymbol.EAN13,
            ZBarSymbol.EAN8,
            ZBarSymbol.UPCA,
            ZBarSymbol.CODE39,
            ZBarSymbol.I25,
        ],
    )
    if not symbols:
        # No barcode detected — can't determine orientation safely. Skip
        # rotation entirely : a wild Hough guess is worse than leaving
        # the photo as-is.
        return gray

    # Use the largest barcode (most likely the receipt's main barcode,
    # not a side-product barcode bleed-through).
    largest = max(symbols, key=lambda s: s.rect.width * s.rect.height)
    polygon = largest.polygon
    if len(polygon) != 4:
        return gray  # malformed detection

    # Compute the angle of the barcode's "long" edge wrt horizontal.
    # Receipt barcodes are usually wider than tall ; the long edge runs
    # parallel to the receipt's text baselines.
    pts = np.array([(p.x, p.y) for p in polygon], dtype=np.float32)
    rect = cv2.minAreaRect(pts)
    # rect = ((cx, cy), (w, h), angle_deg). cv2.minAreaRect returns
    # angle in (-90, 0]. We need the angle of the long edge.
    (_, _), (w, h), angle = rect
    if w < h:
        angle = angle + 90.0
    # Normalize to (-45, 45] via modulo. OpenCV 4.5+ returns minAreaRect
    # angle in [0, 90) ; combined with the +90 above, the raw value can
    # land at 90°. The previous single-step normalization
    # (`if angle > 45: angle -= 90 ; if angle < -45: angle += 90`)
    # missed cases where the value needed two shifts. Modulo guarantees
    # a single canonical result : ((x + 45) mod 90) - 45  ∈  (-45, 45].
    angle = ((angle + 45.0) % 90.0) - 45.0

    if abs(angle) < 0.5:
        return gray  # already straight

    h_img, w_img = gray.shape[:2]
    center = (w_img // 2, h_img // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        gray,
        M,
        (w_img, h_img),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _correct_brightness(gray: np.ndarray) -> np.ndarray:
    """Stretch histogram to [0, 255] if image is over/under-exposed."""
    min_val, max_val = int(gray.min()), int(gray.max())
    if max_val - min_val < 10:
        return gray
    # Pre-allocate the destination (same shape/dtype) rather than passing
    # ``None`` : the cv2 stubs type ``dst`` as a required ``MatLike`` and do
    # not model the OpenCV ``dst=None`` auto-allocation overload. normalize
    # overwrites every element, so this is equivalent to the implicit alloc.
    dst: np.ndarray = np.empty_like(gray)
    return cv2.normalize(gray, dst, 0, 255, cv2.NORM_MINMAX)
