"""Pattern C — verify ratis_batch_achievements calls init_sentry in main()."""

from __future__ import annotations

from batch.ratis_batch_achievements import __main__ as achievements_main


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(achievements_main, "init_sentry", _capture_then_raise)

    try:
        achievements_main.main()
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_achievements"]
