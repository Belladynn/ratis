from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from worker.ocr.types import OcrResult

_RawBlock = tuple[list[list[float]], tuple[str, float]]


@dataclass(frozen=True)
class RichOcrBlock:
    """OCR block enriched with positional info (kept by `recognize_rich`).

    Used by the LLM filter step (AF-12 part 2) which needs to distinguish
    header / items / footer by Y-position. The legacy `recognize()` keeps
    its (text, confidence) shape for backward compat with the existing
    arbitrator pipeline.
    """

    text: str
    confidence: float
    # Bounding box as 4 corners (x, y) — top-left, top-right, bottom-right, bottom-left.
    bbox: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]

    @property
    def center_x(self) -> float:
        return sum(p[0] for p in self.bbox) / 4

    @property
    def center_y(self) -> float:
        return sum(p[1] for p in self.bbox) / 4

    @property
    def height(self) -> float:
        ys = [p[1] for p in self.bbox]
        return max(ys) - min(ys)

    @property
    def width(self) -> float:
        xs = [p[0] for p in self.bbox]
        return max(xs) - min(xs)

    def to_dict(self) -> dict:
        """JSON-serializable form for logging / fixture capture / LLM input."""
        return {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "x": round(self.center_x, 1),
            "y": round(self.center_y, 1),
            "w": round(self.width, 1),
            "h": round(self.height, 1),
        }


class OcrEngine(Protocol):
    def recognize(self, image: np.ndarray) -> OcrResult: ...

    def recognize_rich(self, image: np.ndarray) -> list[RichOcrBlock]: ...


class PaddleOcrEngine:
    """Production OCR engine backed by PaddleOCR."""

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("paddleocr is not installed. Install paddlepaddle-cpu and paddleocr.") from exc
        self._ocr = PaddleOCR(use_angle_cls=True, lang="fr", show_log=False)

    def recognize(self, image: np.ndarray) -> OcrResult:
        raw = self._ocr.ocr(image, cls=True)
        if not raw or not raw[0]:
            return []
        blocks = [(line[0], line[1]) for line in raw[0] if line[1][0].strip()]
        return _spatial_sort(blocks)

    def recognize_rich(self, image: np.ndarray) -> list[RichOcrBlock]:
        """Same OCR pass as `recognize`, but keeps bounding boxes for the
        LLM filter step. The blocks are NOT spatially sorted — the LLM
        gets the raw output and we let it use the positions itself.
        """
        raw = self._ocr.ocr(image, cls=True)
        if not raw or not raw[0]:
            return []
        result: list[RichOcrBlock] = []
        for line in raw[0]:
            bbox, (text, conf) = line
            text = text.strip()
            if not text:
                continue
            # bbox from PaddleOCR is list[list[float]] of 4 points, we
            # convert to a fixed tuple-of-tuples for hashability + safety.
            bbox_tuple = tuple((float(p[0]), float(p[1])) for p in bbox)
            if len(bbox_tuple) != 4:
                continue
            result.append(
                RichOcrBlock(
                    text=text,
                    confidence=float(conf),
                    bbox=bbox_tuple,  # type: ignore[arg-type]
                )
            )
        return result


def _spatial_sort(raw_blocks: list[_RawBlock]) -> OcrResult:
    """
    Re-order PaddleOCR raw blocks into natural reading order (left-to-right,
    top-to-bottom), correcting column-by-column output on two-column receipts.

    Each raw block is [bbox_4pts, (text, confidence)] where bbox_4pts is
    [[x0,y0],[x1,y1],[x2,y2],[x3,y3]] (top-left, top-right, bottom-right, bottom-left).

    Algorithm:
    1. Compute center_x, center_y and height for each block.
    2. Estimate line_tolerance = median(heights) * 0.6.
    3. Sort blocks by center_y, then greedily group into rows: a block joins the
       current row if |its center_y − row's mean center_y| < line_tolerance.
    4. Within each row, sort blocks by center_x (left → right).
    5. Flatten rows top-to-bottom.
    """
    if not raw_blocks:
        return []

    parsed: list[tuple[float, float, float, str, float]] = []  # cx, cy, height, text, conf
    for bbox, (text, conf) in raw_blocks:
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        height = max(ys) - min(ys)
        parsed.append((cx, cy, height, text.strip(), float(conf)))

    heights = [h for _, _, h, _, _ in parsed]
    tolerance = statistics.median(heights) * 0.6

    # Sort by center_y to process top-to-bottom
    parsed.sort(key=lambda b: b[1])

    rows: list[list[tuple[float, float, float, str, float]]] = []
    for block in parsed:
        cy = block[1]
        placed = False
        for row in rows:
            row_mean_cy = sum(b[1] for b in row) / len(row)
            if abs(cy - row_mean_cy) < tolerance:
                row.append(block)
                placed = True
                break
        if not placed:
            rows.append([block])

    result: OcrResult = []
    for row in rows:
        row.sort(key=lambda b: b[0])  # sort by center_x
        for _cx, _cy, _h, text, conf in row:
            result.append((text, conf))
    return result
