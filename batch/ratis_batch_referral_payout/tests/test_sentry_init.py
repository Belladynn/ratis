"""Pattern C — verify ratis_batch_referral_payout calls init_sentry in main()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import payout


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["payout.py", "--dry-run"])
    monkeypatch.setattr(payout, "make_engine", MagicMock())
    monkeypatch.setattr(payout, "sessionmaker", MagicMock())
    monkeypatch.setattr(payout, "run", MagicMock(return_value={"processed": 0}))

    with patch.object(payout, "init_sentry") as mock_init:
        payout.main()

    mock_init.assert_called_once_with("ratis_batch_referral_payout")
