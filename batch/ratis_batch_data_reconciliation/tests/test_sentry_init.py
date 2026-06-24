"""Pattern C — verify ratis_batch_data_reconciliation calls init_sentry in main()."""

from __future__ import annotations

import run as data_reconciliation_run


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(data_reconciliation_run, "init_sentry", _capture_then_raise)

    try:
        data_reconciliation_run.main(dry_run=True)
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_data_reconciliation"]
