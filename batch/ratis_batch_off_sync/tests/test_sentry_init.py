"""Pattern C — verify ratis_batch_off_sync calls init_sentry in main()."""

from __future__ import annotations

from off_sync import main as off_sync_main


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    """Patch init_sentry to raise after recording — early exit avoids touching
    the async API / DB layers and keeps the test hermetic."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(off_sync_main, "init_sentry", _capture_then_raise)

    try:
        off_sync_main.main(argv=["--mode", "delta", "--dry-run", "--workers", "1"])
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_off_sync"]
