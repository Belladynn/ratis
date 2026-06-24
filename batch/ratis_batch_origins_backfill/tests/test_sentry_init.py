"""Pattern C — verify ratis_batch_origins_backfill calls init_sentry."""

from __future__ import annotations

from origins_backfill import main as backfill_main


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    """Patch init_sentry to raise after recording — early exit avoids
    touching the HTTP / DB layers and keeps the test hermetic."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(backfill_main, "init_sentry", _capture_then_raise)

    try:
        backfill_main.main(argv=["--max-eans", "0", "--request-delay-sec", "0"])
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_origins_backfill"]


def test_main_aborts_when_database_url_missing(monkeypatch):
    """``DATABASE_URL`` is mandatory — the entrypoint exits with code 1."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # init_sentry must still be called first (silent no-op when SENTRY_DSN unset).
    monkeypatch.setattr(backfill_main, "init_sentry", lambda _name: None)

    rc = backfill_main.main(argv=["--request-delay-sec", "0"])
    assert rc == 1
