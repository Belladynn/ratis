"""Pattern C — verify ratis_batch_vrac_seed calls init_sentry in main().

The init_sentry helper itself is tested in
``webservices/ratis_rewards/tests/test_middleware.py`` ; here we only
guarantee the batch entrypoint invokes it before doing any work.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import vrac_seed


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    """init_sentry must be called with the canonical batch name."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["vrac_seed.py", "--dry-run"])
    monkeypatch.setattr(vrac_seed, "make_engine", MagicMock())
    monkeypatch.setattr(vrac_seed, "sessionmaker", MagicMock())
    monkeypatch.setattr(vrac_seed, "seed_products", MagicMock(return_value={"inserted": 0, "skipped": 0}))

    with patch.object(vrac_seed, "init_sentry") as mock_init:
        vrac_seed.main()

    mock_init.assert_called_once_with("ratis_batch_vrac_seed")


def test_main_calls_init_sentry_before_db_access(monkeypatch):
    """Sentry init must precede DB engine creation so engine-creation errors are captured."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["vrac_seed.py", "--dry-run"])

    call_order: list[str] = []
    monkeypatch.setattr(
        vrac_seed,
        "make_engine",
        MagicMock(side_effect=lambda *a, **kw: call_order.append("make_engine") or MagicMock()),
    )
    monkeypatch.setattr(vrac_seed, "sessionmaker", MagicMock())
    monkeypatch.setattr(vrac_seed, "seed_products", MagicMock(return_value={"inserted": 0, "skipped": 0}))

    with patch.object(
        vrac_seed,
        "init_sentry",
        side_effect=lambda name: call_order.append("init_sentry"),
    ):
        vrac_seed.main()

    assert call_order[0] == "init_sentry", f"init_sentry must precede make_engine — call order was {call_order}"
