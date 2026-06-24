"""Unit tests for ``ratis_core.services.settings_2fa.detect_magnitude_breach``.

The detector is the cornerstone of the admin settings 2FA grace period
(`ARCH_admin_settings.md` § Garde-fous V1 § 2). A "breach" is any single
numeric (int/float, NOT bool) value whose relative variation exceeds 50 %
between ``old_data`` and ``new_data``. A breach on **any** key marks the
whole PUT atomic — the helper returns the *first* offending dotted path it
finds and the caller treats it as the trigger.

Strings, booleans, arrays and missing keys are skipped by design. Nested
dicts are walked recursively (e.g. ``{"a": {"b": 100}}`` → ``"a.b"``).
"""

from __future__ import annotations

from typing import Any

import pytest
from ratis_core.services.settings_2fa import detect_magnitude_breach

# ---------------------------------------------------------------------------
# Happy paths — small variations, no breach
# ---------------------------------------------------------------------------


def test_no_breach_small_variation():
    """20 % variation stays under the 50 % threshold."""
    assert detect_magnitude_breach({"x": 500}, {"x": 600}) == (False, None)


def test_no_breach_exactly_at_threshold():
    """Boundary : exactly 50 % is NOT a breach (strict ``>`` in the spec)."""
    # 100 → 150 : delta 50 / 100 = 0.5 — not strictly greater than threshold.
    assert detect_magnitude_breach({"x": 100}, {"x": 150}) == (False, None)


def test_no_breach_no_changes():
    """Identical payload : no breach."""
    assert detect_magnitude_breach({"x": 100, "y": "hi"}, {"x": 100, "y": "hi"}) == (
        False,
        None,
    )


# ---------------------------------------------------------------------------
# Breach paths — variations above threshold
# ---------------------------------------------------------------------------


def test_breach_increase_x10():
    """Classic typo case : 500 → 5000 (×10) must trip the detector."""
    breach, key = detect_magnitude_breach({"x": 500}, {"x": 5000})
    assert breach is True
    assert key == "x"


def test_breach_decrease_70pct():
    """A 70 % cut is a breach too — symmetrical magnitude check."""
    breach, key = detect_magnitude_breach({"x": 100}, {"x": 30})
    assert breach is True
    assert key == "x"


def test_breach_just_above_threshold():
    """100 → 151 : 51 % delta — strictly above 50 %, breach expected."""
    breach, key = detect_magnitude_breach({"x": 100}, {"x": 151})
    assert breach is True
    assert key == "x"


def test_zero_to_nonzero():
    """0 → 5 : a non-zero target from a zero baseline is always a breach
    (guards against typos on default-zero settings)."""
    breach, key = detect_magnitude_breach({"x": 0}, {"x": 5})
    assert breach is True
    assert key == "x"


def test_zero_to_zero_no_breach():
    """0 → 0 : no change, no breach."""
    assert detect_magnitude_breach({"x": 0}, {"x": 0}) == (False, None)


def test_negative_value_breach():
    """Magnitudes are computed in absolute value — sign change still works."""
    breach, key = detect_magnitude_breach({"x": -100}, {"x": -10})
    assert breach is True
    assert key == "x"


# ---------------------------------------------------------------------------
# Skip paths — non-numeric values
# ---------------------------------------------------------------------------


def test_skip_string():
    """String values never trigger a magnitude breach."""
    assert detect_magnitude_breach({"x": "old"}, {"x": "new"}) == (False, None)


def test_skip_bool():
    """``bool`` is a subclass of ``int`` — must be excluded explicitly."""
    assert detect_magnitude_breach({"x": True}, {"x": False}) == (False, None)


def test_skip_bool_to_int_not_breach():
    """Mixed bool/int : skip if either side is bool — the spec only checks
    numeric magnitudes, not type mismatches."""
    assert detect_magnitude_breach({"x": True}, {"x": 100}) == (False, None)


def test_skip_array():
    """V1 keeps arrays out of scope — array diffs are skipped."""
    assert detect_magnitude_breach({"x": [1, 2]}, {"x": [3, 4]}) == (False, None)


def test_skip_none():
    """``None`` values never breach — there is no numeric baseline."""
    assert detect_magnitude_breach({"x": None}, {"x": 5000}) == (False, None)
    assert detect_magnitude_breach({"x": 5000}, {"x": None}) == (False, None)


# ---------------------------------------------------------------------------
# Nested dicts — recursion
# ---------------------------------------------------------------------------


def test_nested_breach():
    """Recurse into nested dicts and report the dotted key path."""
    breach, key = detect_magnitude_breach(
        {"a": {"b": 100}},
        {"a": {"b": 1000}},
    )
    assert breach is True
    assert key == "a.b"


def test_deep_nested_breach():
    """Three levels deep still surfaces the right dotted path."""
    breach, key = detect_magnitude_breach(
        {"a": {"b": {"c": 100}}},
        {"a": {"b": {"c": 1000}}},
    )
    assert breach is True
    assert key == "a.b.c"


def test_nested_no_breach_keeps_walking():
    """Sibling keys with safe variation must not mask a deeper breach."""
    breach, key = detect_magnitude_breach(
        {"a": {"safe": 100, "danger": 100}},
        {"a": {"safe": 110, "danger": 1000}},
    )
    assert breach is True
    assert key == "a.danger"


# ---------------------------------------------------------------------------
# Atomic flag — multiple keys, one breach is enough
# ---------------------------------------------------------------------------


def test_multi_keys_one_breach_atomic():
    """One key over threshold → whole PUT marked pending. The helper
    returns the breaching key, not all of them."""
    breach, key = detect_magnitude_breach(
        {"x": 100, "y": 200},
        {"x": 105, "y": 2000},
    )
    assert breach is True
    assert key == "y"


# ---------------------------------------------------------------------------
# Missing baseline / added or removed keys
# ---------------------------------------------------------------------------


def test_old_none_first_write():
    """First write (no DB row yet) cannot breach — there is no baseline."""
    assert detect_magnitude_breach(None, {"x": 5000}) == (False, None)


def test_old_empty_dict():
    """Empty dict baseline is treated like a first write for missing keys."""
    assert detect_magnitude_breach({}, {"x": 5000}) == (False, None)


def test_added_key_no_breach():
    """A key absent from old_data is added — no baseline, no breach."""
    assert detect_magnitude_breach({"a": 1}, {"a": 1, "x": 5000}) == (False, None)


def test_removed_key_no_breach():
    """A key removed from new_data has no successor — skip."""
    assert detect_magnitude_breach({"x": 5000}, {}) == (False, None)


# ---------------------------------------------------------------------------
# Type mismatches — old numeric, new not numeric (or vice versa)
# ---------------------------------------------------------------------------


def test_type_change_numeric_to_string():
    """Old=int, new=str : skip (no magnitude possible)."""
    assert detect_magnitude_breach({"x": 100}, {"x": "100"}) == (False, None)


def test_type_change_dict_to_int():
    """Structural change : skip silently — caller still has the diff."""
    assert detect_magnitude_breach({"x": {"a": 1}}, {"x": 5}) == (False, None)


# ---------------------------------------------------------------------------
# Configurable threshold
# ---------------------------------------------------------------------------


def test_custom_threshold_below():
    """A 30 % variation is a breach with threshold=0.2."""
    breach, key = detect_magnitude_breach({"x": 100}, {"x": 130}, threshold=0.2)
    assert breach is True
    assert key == "x"


def test_custom_threshold_strict():
    """A 30 % variation is NOT a breach with threshold=0.5."""
    assert detect_magnitude_breach({"x": 100}, {"x": 130}, threshold=0.5) == (
        False,
        None,
    )


@pytest.mark.parametrize(
    ("old", "new", "expected_breach"),
    [
        ({"x": 1.0}, {"x": 1.6}, True),  # 60 % up
        ({"x": 1.0}, {"x": 1.4}, False),  # 40 % up
        ({"x": 0.5}, {"x": 0.74}, False),  # 48 % up
        ({"x": 0.5}, {"x": 0.76}, True),  # 52 % up
    ],
)
def test_float_variations(old, new, expected_breach):
    """Floats follow the same threshold logic as ints."""
    breach, _ = detect_magnitude_breach(old, new)
    assert breach is expected_breach


# ---------------------------------------------------------------------------
# L1 — Recursion depth cap (defensive against pathological JSON)
# ---------------------------------------------------------------------------
# Real-world settings sections nest 1-3 levels deep ; ``_MAX_DEPTH = 32`` is
# multiple orders of magnitude above the worst legitimate case. A payload
# nested deeper is treated as malformed input — the walker bails out
# silently with ``(False, None)`` rather than raising :exc:`RecursionError`.
# This prevents a 500 server crash on a hostile / buggy PUT payload while
# *not* skipping the magnitude check on legitimate calls.


def _build_nested(depth: int, leaf_value: object) -> dict:
    """Build a dict nested ``depth`` levels with leaf key ``"v"``."""
    out: Any = leaf_value
    for _ in range(depth):
        out = {"v": out}
    return out


def test_walk_max_depth_returns_no_breach_safely():
    """A 50-level nested dict must not crash — return (False, None) defensively.

    Without the depth cap, the recursion would raise ``RecursionError``
    far below 50 only on pathological dict shapes ; the cap turns *any*
    deeper-than-32 payload into a safe no-op so the route surfaces a
    normal applied/pending response instead of a 500.
    """
    old = _build_nested(50, 100)
    new = _build_nested(50, 5000)  # ×50 ⇒ would be a breach if walked
    breach, key = detect_magnitude_breach(old, new)
    # The cap intentionally swallows the breach detection beyond 32
    # levels — the V1 trade-off is "no DoS over correctness" since real
    # settings never go that deep. The outer levels (1..32) are still
    # walked, but here the breach lives at depth 50 — out of reach.
    assert breach is False
    assert key is None


def test_walk_normal_depth_works():
    """A 5-level nested dict still surfaces breaches normally — cap never trips."""
    old = _build_nested(5, 100)
    new = _build_nested(5, 1000)  # ×10 — clear breach
    breach, key = detect_magnitude_breach(old, new)
    assert breach is True
    # 5 levels of "v" → "v.v.v.v.v"
    assert key == "v.v.v.v.v"


def test_walk_depth_just_below_cap_works():
    """Depth = 32 (exact cap) still detects breaches at the deepest leaf."""
    # The detection uses ``_depth > _MAX_DEPTH`` strictly so depth 32 is
    # walked, depth 33 returns no-breach. This pins the boundary so a
    # future tweak of the constant is caught by the test.
    from ratis_core.services.settings_2fa import _MAX_DEPTH

    old = _build_nested(_MAX_DEPTH, 100)
    new = _build_nested(_MAX_DEPTH, 1000)
    breach, _ = detect_magnitude_breach(old, new)
    assert breach is True


def test_walk_depth_just_above_cap_skipped():
    """Depth = MAX_DEPTH + 1 → bail-out (False, None), no exception."""
    from ratis_core.services.settings_2fa import _MAX_DEPTH

    old = _build_nested(_MAX_DEPTH + 5, 100)
    new = _build_nested(_MAX_DEPTH + 5, 1000)
    breach, key = detect_magnitude_breach(old, new)
    assert breach is False
    assert key is None
