"""Tests for off_sync.main — _compute_range logic, no DB, no HTTP."""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import pytest
from off_sync.main import DELTA_OVERLAP_SECONDS, _compute_range, _parse_args, _to_ts


def _args(**kwargs) -> argparse.Namespace:
    """Build a minimal Namespace with safe defaults."""
    defaults = {"mode": None, "since": None, "until": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ── delta mode — last_success_ts available ────────────────────────────────────


def test_delta_uses_last_success_ts_minus_overlap():
    """delta + log entry → last_success_ts - DELTA_OVERLAP_SECONDS."""
    ts = 1_700_000_000
    since, until = _compute_range(_args(mode="delta"), last_success_ts=ts)
    assert since == ts - DELTA_OVERLAP_SECONDS
    assert until is None


def test_delta_overlap_is_5_minutes():
    """Overlap constant must be 5 minutes (300 seconds)."""
    assert DELTA_OVERLAP_SECONDS == 300


def test_delta_overlap_applied_exactly():
    """Overlap shifts since_ts back by exactly 5 min regardless of ts value."""
    for ts in (0, 1_000, 1_700_000_000):
        since, _ = _compute_range(_args(mode="delta"), last_success_ts=ts)
        assert since == ts - DELTA_OVERLAP_SECONDS


# ── delta mode — no log entry (first run) ─────────────────────────────────────


def test_delta_fallback_to_yesterday_when_no_log():
    """delta + no log → midnight yesterday UTC (no overlap applied)."""
    since, until = _compute_range(_args(mode="delta"), last_success_ts=None)
    expected = _to_ts(date.today() - timedelta(days=1))
    assert since == expected
    assert until is None


# ── weekly / monthly modes ────────────────────────────────────────────────────


def test_weekly_uses_fixed_window():
    """weekly → 7 days ago midnight UTC, no overlap."""
    since, until = _compute_range(_args(mode="weekly"))
    expected = _to_ts(date.today() - timedelta(days=7))
    assert since == expected
    assert until is None


def test_monthly_uses_fixed_window():
    """monthly → 30 days ago midnight UTC, no overlap."""
    since, until = _compute_range(_args(mode="monthly"))
    expected = _to_ts(date.today() - timedelta(days=30))
    assert since == expected
    assert until is None


# ── explicit --since / --until ────────────────────────────────────────────────


def test_explicit_since_without_until():
    since, until = _compute_range(_args(since=date(2026, 1, 1)))
    assert since == _to_ts(date(2026, 1, 1))
    assert until is None


def test_explicit_since_with_until():
    since, until = _compute_range(_args(since=date(2026, 1, 1), until=date(2026, 1, 31)))
    assert since == _to_ts(date(2026, 1, 1))
    assert until == _to_ts(date(2026, 1, 31))


# ── --force-resync flag ───────────────────────────────────────────────────────


def test_force_resync_flag_parses_with_mode():
    """--force-resync is accepted alongside an API mode."""
    ns = _parse_args(["--mode", "delta", "--force-resync"])
    assert ns.mode == "delta"
    assert ns.force_resync is True


def test_force_resync_default_false():
    """Without --force-resync, the flag defaults to False."""
    ns = _parse_args(["--mode", "weekly"])
    assert ns.force_resync is False


def test_force_resync_rejected_with_mode_full():
    """--force-resync + --mode full is redundant and refused at parse time."""
    with pytest.raises(SystemExit):
        _parse_args(["--mode", "full", "--dump", "/tmp/x.jsonl.gz", "--force-resync"])


def test_force_resync_accepted_with_explicit_since():
    """--force-resync + explicit --since is allowed (since_ts gets overridden in main)."""
    ns = _parse_args(["--since", "2026-01-01", "--force-resync"])
    assert ns.force_resync is True
    assert ns.since == date(2026, 1, 1)


# ── --source flag (multi-source pluggable batch) ──────────────────────────────


def test_source_flag_defaults_to_off():
    ns = _parse_args(["--mode", "delta"])
    assert ns.source == "off"


def test_source_flag_accepts_obp():
    ns = _parse_args(["--mode", "delta", "--source", "obp"])
    assert ns.source == "obp"


def test_source_flag_accepts_opf():
    ns = _parse_args(["--mode", "delta", "--source", "opf"])
    assert ns.source == "opf"


def test_source_flag_accepts_opff():
    ns = _parse_args(["--mode", "delta", "--source", "opff"])
    assert ns.source == "opff"


def test_source_flag_rejects_unknown():
    with pytest.raises(SystemExit):
        _parse_args(["--mode", "delta", "--source", "gs1"])


def test_batch_name_resolves_per_source():
    """BATCH_NAME constant gives way to source.batch_name lookup."""
    from off_sync.sources import get_source

    assert get_source("off").batch_name == "off_sync"
    assert get_source("obp").batch_name == "obp_sync"
    assert get_source("opf").batch_name == "opf_sync"
    assert get_source("opff").batch_name == "opff_sync"
