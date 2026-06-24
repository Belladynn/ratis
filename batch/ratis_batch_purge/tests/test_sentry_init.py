"""Pattern C — verify ratis_batch_purge calls init_sentry in main()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import purge


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["purge.py", "--dry-run"])
    monkeypatch.setattr(purge, "make_engine", MagicMock())
    monkeypatch.setattr(purge, "sessionmaker", MagicMock())
    monkeypatch.setattr(purge, "STEPS", [])  # zero steps = no DB work
    monkeypatch.setattr(purge, "_write_sync_log", MagicMock())

    with patch.object(purge, "init_sentry") as mock_init:
        purge.main()

    mock_init.assert_called_once_with("ratis_batch_purge")
