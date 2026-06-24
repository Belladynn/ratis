"""Pattern C — verify ratis_batch_mystery_announce calls init_sentry in main()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import mystery_announce


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["mystery_announce.py", "--dry-run"])
    monkeypatch.setattr(mystery_announce, "make_engine", MagicMock())
    monkeypatch.setattr(mystery_announce, "sessionmaker", MagicMock())
    monkeypatch.setattr(mystery_announce, "STEPS", [])  # zero steps = no DB work
    monkeypatch.setattr(mystery_announce, "_write_sync_log", MagicMock())

    with patch.object(mystery_announce, "init_sentry") as mock_init:
        mystery_announce.main()

    mock_init.assert_called_once_with("ratis_batch_mystery_announce")
