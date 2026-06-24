"""Pattern C — verify ratis_batch_consensus calls init_sentry in main()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import consensus


def _fake_settings() -> dict:
    """Minimal settings covering both phase-1+2 and phase-3 required keys."""
    return {
        "consensus": {
            "window_size": 20,
            "scan_weight_floor": 0.1,
            "scan_weight_decay_per_day": 0.01,
            "emerging_consecutive_threshold": 3,
            "emerging_old_weight": 0.3,
            "decay_grace_days": 90,
            "decay_rate_pct": 5,
            "decay_floor": 50,
            "freeze_threshold_scans": 10,
            "freeze_duration_hours": 24,
            "batch_chunk_size": 100,
            "batch_max_workers": 4,
        },
        "store_validation": {
            "min_distinct_eans_for_validation": 10,
            "consensus_min_trust_score": 80,
            "suspicious_after_months": 3,
            "suspicious_threshold_eans": 5,
        },
    }


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["consensus.py", "--dry-run"])
    monkeypatch.setattr(consensus, "make_engine", MagicMock())
    monkeypatch.setattr(consensus, "sessionmaker", MagicMock())
    monkeypatch.setattr(consensus, "load_settings", _fake_settings)
    monkeypatch.setattr(consensus, "run_batch", MagicMock(return_value=([], 0)))
    monkeypatch.setattr(consensus, "run_store_validation_phase", MagicMock(return_value={}))
    monkeypatch.setattr(consensus, "_write_sync_log", MagicMock())

    with patch.object(consensus, "init_sentry") as mock_init:
        consensus.main()

    mock_init.assert_called_once_with("ratis_batch_consensus")
