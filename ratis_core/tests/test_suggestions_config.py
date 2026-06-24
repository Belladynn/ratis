"""Tests for the curated suggestions config loader."""

from __future__ import annotations

import pytest
from ratis_core.suggestions_config import load_curated_eans


@pytest.fixture(autouse=True)
def _reset_cache():
    """Ensure each test sees a fresh module-level cache."""
    load_curated_eans.cache_clear()
    yield
    load_curated_eans.cache_clear()


def test_load_curated_eans_returns_list_of_strings():
    eans = load_curated_eans()
    assert isinstance(eans, list)
    assert len(eans) > 0
    assert all(isinstance(e, str) for e in eans)
    assert all(len(e) >= 8 for e in eans)  # EAN-8 or EAN-13


def test_load_curated_eans_preserves_order():
    eans = load_curated_eans()
    # Reload — same order (lru_cache idempotency)
    again = load_curated_eans()
    assert eans == again


def test_load_curated_eans_missing_file_raises(tmp_path, monkeypatch):
    fake_path = tmp_path / "missing.json"
    monkeypatch.setattr(
        "ratis_core.suggestions_config._CONFIG_PATH",
        fake_path,
    )
    load_curated_eans.cache_clear()
    with pytest.raises(RuntimeError, match="curated_suggestions"):
        load_curated_eans()


def test_load_curated_eans_invalid_json_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{this is not valid json", encoding="utf-8")
    monkeypatch.setattr(
        "ratis_core.suggestions_config._CONFIG_PATH",
        bad,
    )
    load_curated_eans.cache_clear()
    with pytest.raises(RuntimeError, match="invalid JSON"):
        load_curated_eans()


def test_load_curated_eans_non_array_raises(tmp_path, monkeypatch):
    notarr = tmp_path / "obj.json"
    notarr.write_text('{"eans": ["123"]}', encoding="utf-8")
    monkeypatch.setattr(
        "ratis_core.suggestions_config._CONFIG_PATH",
        notarr,
    )
    load_curated_eans.cache_clear()
    with pytest.raises(RuntimeError, match="must be a JSON array"):
        load_curated_eans()
