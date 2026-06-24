"""Unit tests for ratis_core.utils."""

import pytest
from ratis_core.utils import strip_str


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("Alice", "Alice"),  # clean string — unchanged
        ("  Alice  ", "Alice"),  # leading/trailing spaces — stripped
        (" ", ""),  # spaces only — empty string (triggers min_length)
        ("", ""),  # already empty — unchanged
        (None, None),  # None — passed through
        (123, 123),  # non-string — passed through
        ("  a  b  ", "a  b"),  # internal spaces preserved
    ],
)
def test_strip_str(input_val, expected):
    assert strip_str(input_val) == expected
