"""Pattern C — verify ratis_batch_savings calls init_sentry in main().

The init_sentry helper itself is tested in
``webservices/ratis_rewards/tests/test_middleware.py`` ; here we only
guarantee the batch entrypoint invokes it before doing any work.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import savings_batch


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["savings_batch.py", "--dry-run"])
    monkeypatch.setattr(savings_batch, "make_engine", MagicMock())
    monkeypatch.setattr(savings_batch, "sessionmaker", MagicMock())
    monkeypatch.setattr(savings_batch, "recompute_all_user_snapshots", MagicMock(return_value=0))
    monkeypatch.setattr(savings_batch, "_write_sync_log", MagicMock())

    with patch.object(savings_batch, "init_sentry") as mock_init:
        savings_batch.main()

    mock_init.assert_called_once_with("ratis_batch_savings")


def test_main_calls_init_sentry_before_db_access(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["savings_batch.py", "--dry-run"])

    call_order: list[str] = []
    monkeypatch.setattr(
        savings_batch,
        "make_engine",
        MagicMock(side_effect=lambda *a, **kw: call_order.append("make_engine") or MagicMock()),
    )
    monkeypatch.setattr(savings_batch, "sessionmaker", MagicMock())
    monkeypatch.setattr(savings_batch, "recompute_all_user_snapshots", MagicMock(return_value=0))
    monkeypatch.setattr(savings_batch, "_write_sync_log", MagicMock())

    with patch.object(
        savings_batch,
        "init_sentry",
        side_effect=lambda name: call_order.append("init_sentry"),
    ):
        savings_batch.main()

    assert call_order[0] == "init_sentry", f"init_sentry must precede make_engine — call order was {call_order}"
