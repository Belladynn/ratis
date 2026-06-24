from __future__ import annotations

from worker.ocr.ocr_engine import _spatial_sort


def _block(text: str, conf: float, x: float, y: float, w: float = 100.0, h: float = 20.0):
    """Build a fake PaddleOCR raw block: [4-corner bbox, (text, conf)]."""
    bbox = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
    return [bbox, (text, conf)]


class TestSpatialSort:
    def test_already_ordered_unchanged(self):
        """Single-column receipt already in reading order — output unchanged."""
        raw = [
            _block("NUTELLA 400G", 0.97, x=10, y=10),
            _block("2,50 B", 0.96, x=10, y=40),
            _block("TOTAL", 0.99, x=10, y=70),
            _block("2,50", 0.99, x=10, y=100),
        ]
        result = _spatial_sort(raw)
        texts = [t for t, _ in result]
        assert texts == ["NUTELLA 400G", "2,50 B", "TOTAL", "2,50"]

    def test_two_column_layout_reordered(self):
        """PaddleOCR reads left column then right column.
        After spatial sort each name is immediately followed by its price."""
        raw = [
            # Left column (names) — x≈10
            _block("NUTELLA 400G", 0.97, x=10, y=10),
            _block("PAIN DE MIE", 0.95, x=10, y=40),
            _block("LAIT", 0.93, x=10, y=70),
            # Right column (prices) — x≈300
            _block("2,50", 0.96, x=300, y=10),
            _block("1,20", 0.94, x=300, y=40),
            _block("0,89", 0.92, x=300, y=70),
        ]
        result = _spatial_sort(raw)
        texts = [t for t, _ in result]
        assert texts == ["NUTELLA 400G", "2,50", "PAIN DE MIE", "1,20", "LAIT", "0,89"]

    def test_three_items_two_columns_preserves_confidence(self):
        """Confidences are preserved in correct order after spatial sort."""
        raw = [
            _block("A", 0.91, x=10, y=10),
            _block("B", 0.92, x=10, y=40),
            _block("1,00", 0.85, x=300, y=10),
            _block("2,00", 0.86, x=300, y=40),
        ]
        result = _spatial_sort(raw)
        assert result == [("A", 0.91), ("1,00", 0.85), ("B", 0.92), ("2,00", 0.86)]

    def test_single_block_returned_as_is(self):
        raw = [_block("NUTELLA", 0.95, x=10, y=10)]
        result = _spatial_sort(raw)
        assert result == [("NUTELLA", 0.95)]

    def test_empty_input(self):
        assert _spatial_sort([]) == []

    def test_blocks_on_same_line_sorted_left_to_right(self):
        """Blocks with very close Y are treated as the same row and sorted by X."""
        raw = [
            _block("PRIX", 0.90, x=300, y=12),  # same row as name, arrives first
            _block("NOM", 0.95, x=10, y=10),
        ]
        result = _spatial_sort(raw)
        texts = [t for t, _ in result]
        assert texts == ["NOM", "PRIX"]

    def test_tolerance_absorbs_slight_y_variation(self):
        """Blocks on the same physical line may have slightly different Y due to bbox tilt."""
        raw = [
            _block("NUTELLA", 0.97, x=10, y=10),
            _block("2,50", 0.96, x=300, y=14),  # 4px offset but same line
        ]
        result = _spatial_sort(raw)
        texts = [t for t, _ in result]
        assert texts == ["NUTELLA", "2,50"]

    def test_mixed_inline_and_column_layout(self):
        """Some lines are full-width (name+price in one block), others are split."""
        raw = [
            _block("NUTELLA 400G         2,50 B", 0.97, x=10, y=10, w=400),  # inline
            _block("PAIN DE MIE", 0.95, x=10, y=40),
            _block("1,20", 0.94, x=300, y=40),
        ]
        result = _spatial_sort(raw)
        texts = [t for t, _ in result]
        assert texts == ["NUTELLA 400G         2,50 B", "PAIN DE MIE", "1,20"]
