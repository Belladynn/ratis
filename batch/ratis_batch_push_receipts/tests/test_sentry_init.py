"""Pattern C — verify ratis_batch_push_receipts calls init_sentry in main()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import push_receipts


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["push_receipts.py", "--dry-run"])
    monkeypatch.setattr(push_receipts, "make_engine", MagicMock())
    monkeypatch.setattr(push_receipts, "sessionmaker", MagicMock())
    monkeypatch.setattr(
        push_receipts,
        "poll_receipts",
        MagicMock(
            return_value={
                "tickets_polled": 0,
                "receipts_received": 0,
                "dead_tokens_removed": 0,
            }
        ),
    )
    monkeypatch.setattr(push_receipts, "_write_sync_log", MagicMock())

    with patch.object(push_receipts, "init_sentry") as mock_init:
        push_receipts.main()

    mock_init.assert_called_once_with("ratis_batch_push_receipts")
