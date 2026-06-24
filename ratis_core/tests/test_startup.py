"""Tests for ratis_core.startup helpers.

Covers :
    - ``require_env`` — pre-existing fail-fast helper (regression).
    - ``require_env_min_length`` — M1 fix : enforce a minimum length on a
      required env var at boot. Raises with a clear message when the value
      is unset, empty, or shorter than the threshold.
"""

from __future__ import annotations

import pytest
from ratis_core.startup import require_env, require_env_min_length

# ---------------------------------------------------------------------------
# require_env (regression)
# ---------------------------------------------------------------------------


def test_require_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("RATIS_TEST_ENV", raising=False)
    with pytest.raises(RuntimeError, match="RATIS_TEST_ENV"):
        require_env("RATIS_TEST_ENV")


def test_require_env_passes_when_set(monkeypatch):
    monkeypatch.setenv("RATIS_TEST_ENV", "value")
    require_env("RATIS_TEST_ENV")  # no raise


# ---------------------------------------------------------------------------
# require_env_min_length — M1 helper for ADMIN_API_KEY (and similar)
# ---------------------------------------------------------------------------


def test_require_env_min_length_raises_when_unset(monkeypatch):
    """Unset / missing env var → RuntimeError with the var name in the message."""
    monkeypatch.delenv("RATIS_TEST_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RATIS_TEST_KEY"):
        require_env_min_length("RATIS_TEST_KEY", 32)


def test_require_env_min_length_raises_when_empty(monkeypatch):
    """Empty string is treated like absent — same fail-fast semantics."""
    monkeypatch.setenv("RATIS_TEST_KEY", "")
    with pytest.raises(RuntimeError, match="RATIS_TEST_KEY"):
        require_env_min_length("RATIS_TEST_KEY", 32)


def test_require_env_min_length_raises_when_short(monkeypatch):
    """Value shorter than the threshold → RuntimeError with the actual length."""
    monkeypatch.setenv("RATIS_TEST_KEY", "abc")
    with pytest.raises(RuntimeError) as exc_info:
        require_env_min_length("RATIS_TEST_KEY", 32)
    msg = str(exc_info.value)
    assert "RATIS_TEST_KEY" in msg
    assert "3" in msg  # actual length in message
    assert "32" in msg  # required min in message


def test_require_env_min_length_passes_when_long_enough(monkeypatch):
    """Value at the threshold → returns it unchanged."""
    val = "x" * 32
    monkeypatch.setenv("RATIS_TEST_KEY", val)
    assert require_env_min_length("RATIS_TEST_KEY", 32) == val


def test_require_env_min_length_passes_when_longer(monkeypatch):
    """Value longer than the threshold → returns it unchanged."""
    val = "y" * 64
    monkeypatch.setenv("RATIS_TEST_KEY", val)
    assert require_env_min_length("RATIS_TEST_KEY", 32) == val


def test_require_env_min_length_just_below_threshold(monkeypatch):
    """31 chars with min=32 → raises (strict ``<`` boundary)."""
    monkeypatch.setenv("RATIS_TEST_KEY", "x" * 31)
    with pytest.raises(RuntimeError):
        require_env_min_length("RATIS_TEST_KEY", 32)
