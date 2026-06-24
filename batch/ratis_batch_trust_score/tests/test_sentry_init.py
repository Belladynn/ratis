"""Pattern C — verify ratis_batch_trust_score calls init_sentry in main()."""

from __future__ import annotations

import trust_score


def test_main_calls_init_sentry_with_batch_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ratis:ratis@host/db")
    monkeypatch.setattr("sys.argv", ["trust_score.py", "--dry-run"])

    called_with: list[str] = []

    def _capture_then_raise(name):
        called_with.append(name)
        raise SystemExit(0)

    monkeypatch.setattr(trust_score, "init_sentry", _capture_then_raise)

    try:
        trust_score.main()
    except SystemExit:
        pass

    assert called_with == ["ratis_batch_trust_score"]
