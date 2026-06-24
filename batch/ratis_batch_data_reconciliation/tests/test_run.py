"""Tests for run.py orchestration.

Focuses on the per-job try/except boundary + structured logging shape.
The actual job behaviour is exercised in their dedicated test files.
"""

from __future__ import annotations

import logging
import os


def test_run_orchestrates_4_jobs(monkeypatch):
    """All 4 job functions are invoked exactly once, in declared order."""
    import run as run_mod
    from data_reconciliation import (
        ean_recovery as m_ean,
    )
    from data_reconciliation import (
        price_disambiguate as m_price,
    )
    from data_reconciliation import (
        retro_cab as m_retro,
    )
    from data_reconciliation import (
        store_mdd_vote as m_mdd,
    )

    invocations: list[str] = []

    def make_recorder(name, payload):
        def _f(db, *, dry_run):
            invocations.append(name)
            return dict(payload)

        return _f

    monkeypatch.setattr(
        m_ean,
        "reconcile_ean_recovery",
        make_recorder("ean_recovery", {"count_processed": 0, "count_resolved": 0, "duration_ms": 1}),
    )
    monkeypatch.setattr(
        m_mdd,
        "reconcile_store_mdd_vote",
        make_recorder("store_mdd_vote", {"count_processed": 0, "stub_phase_2": True}),
    )
    monkeypatch.setattr(
        m_price,
        "reconcile_price_disambiguate",
        make_recorder("price_disambiguate", {"count_processed": 0, "stub_phase_2": True}),
    )
    monkeypatch.setattr(
        m_retro,
        "reconcile_retro_cab",
        make_recorder("retro_cab", {"count_users_notified": 0, "duration_ms": 1}),
    )

    # Run module imports the job functions at import time, so we also
    # need to patch the bound names inside run.JOBS.
    monkeypatch.setattr(
        run_mod,
        "JOBS",
        [
            ("ean_recovery", m_ean.reconcile_ean_recovery),
            ("store_mdd_vote", m_mdd.reconcile_store_mdd_vote),
            ("price_disambiguate", m_price.reconcile_price_disambiguate),
            ("retro_cab", m_retro.reconcile_retro_cab),
        ],
    )

    stats = run_mod.main(dry_run=True)

    assert invocations == ["ean_recovery", "store_mdd_vote", "price_disambiguate", "retro_cab"]
    assert set(stats.keys()) == {"ean_recovery", "store_mdd_vote", "price_disambiguate", "retro_cab"}


def test_run_continues_if_job_fails(monkeypatch, caplog):
    """A raising job is captured ; subsequent jobs still run."""
    import run as run_mod
    from data_reconciliation import (
        ean_recovery as m_ean,
    )
    from data_reconciliation import (
        price_disambiguate as m_price,
    )
    from data_reconciliation import (
        retro_cab as m_retro,
    )
    from data_reconciliation import (
        store_mdd_vote as m_mdd,
    )

    invocations: list[str] = []

    def boom(db, *, dry_run):
        raise RuntimeError("kaboom")

    def ok(name):
        def _f(db, *, dry_run):
            invocations.append(name)
            return {"count_processed": 0}

        return _f

    monkeypatch.setattr(m_ean, "reconcile_ean_recovery", boom)
    monkeypatch.setattr(m_mdd, "reconcile_store_mdd_vote", ok("store_mdd_vote"))
    monkeypatch.setattr(m_price, "reconcile_price_disambiguate", ok("price_disambiguate"))
    monkeypatch.setattr(m_retro, "reconcile_retro_cab", ok("retro_cab"))

    monkeypatch.setattr(
        run_mod,
        "JOBS",
        [
            ("ean_recovery", m_ean.reconcile_ean_recovery),
            ("store_mdd_vote", m_mdd.reconcile_store_mdd_vote),
            ("price_disambiguate", m_price.reconcile_price_disambiguate),
            ("retro_cab", m_retro.reconcile_retro_cab),
        ],
    )

    with caplog.at_level(logging.ERROR, logger="data_reconciliation"):
        stats = run_mod.main(dry_run=True)

    assert invocations == ["store_mdd_vote", "price_disambiguate", "retro_cab"]
    assert stats["ean_recovery"] == {"error": "kaboom"}
    assert "store_mdd_vote" in stats
    # The error log carries the job name + structured error.
    assert any("ean_recovery" in r.message and "kaboom" in r.message for r in caplog.records)


def test_run_logs_structured(monkeypatch, caplog):
    """End-of-run log carries an event marker + per-job stats payload."""
    import run as run_mod
    from data_reconciliation import (
        ean_recovery as m_ean,
    )
    from data_reconciliation import (
        price_disambiguate as m_price,
    )
    from data_reconciliation import (
        retro_cab as m_retro,
    )
    from data_reconciliation import (
        store_mdd_vote as m_mdd,
    )

    monkeypatch.setattr(m_ean, "reconcile_ean_recovery", lambda db, *, dry_run: {"count_resolved": 3})
    monkeypatch.setattr(m_mdd, "reconcile_store_mdd_vote", lambda db, *, dry_run: {"stub_phase_2": True})
    monkeypatch.setattr(m_price, "reconcile_price_disambiguate", lambda db, *, dry_run: {"stub_phase_2": True})
    monkeypatch.setattr(m_retro, "reconcile_retro_cab", lambda db, *, dry_run: {"count_users_notified": 1})

    monkeypatch.setattr(
        run_mod,
        "JOBS",
        [
            ("ean_recovery", m_ean.reconcile_ean_recovery),
            ("store_mdd_vote", m_mdd.reconcile_store_mdd_vote),
            ("price_disambiguate", m_price.reconcile_price_disambiguate),
            ("retro_cab", m_retro.reconcile_retro_cab),
        ],
    )

    with caplog.at_level(logging.INFO, logger="data_reconciliation"):
        run_mod.main(dry_run=False)

    # The final aggregated log carries the canonical event marker.
    final_lines = [r.message for r in caplog.records if "batch_complete" in r.message]
    assert final_lines, "expected a batch_complete log line"
    assert "ean_recovery" in final_lines[0]
    assert "retro_cab" in final_lines[0]


def test_run_aborts_when_database_url_missing(monkeypatch):
    """No DATABASE_URL → exit(1) before touching any job."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib

    import run as run_mod

    importlib.reload(run_mod)

    import pytest

    with pytest.raises(SystemExit) as excinfo:
        run_mod.main(dry_run=True)
    assert excinfo.value.code == 1

    # Restore for other tests in the session.
    os.environ["DATABASE_URL"] = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_test",
    )
    importlib.reload(run_mod)
