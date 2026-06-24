"""Tests for ratis_core.database.make_engine connection-pool defaults.

Web services call make_engine without kwargs; stale connections after a
network blip or PG restart must be recycled (pool_pre_ping) and pruned
(pool_recycle). Batches already pass pool_pre_ping=True explicitly — the
defaults must be a no-op when an explicit kwarg is given.
"""

from __future__ import annotations

from ratis_core.database import make_engine

_URL = "postgresql+psycopg://u:p@localhost:5432/ratis_dev"  # pragma: allowlist secret


def test_make_engine_defaults_pool_pre_ping_true():
    """No-kwarg call (web services) → pool_pre_ping enabled."""
    engine = make_engine(_URL)
    assert engine.pool._pre_ping is True
    engine.dispose()


def test_make_engine_defaults_pool_recycle_1800():
    """No-kwarg call → connections older than 1800s are recycled."""
    engine = make_engine(_URL)
    assert engine.pool._recycle == 1800
    engine.dispose()


def test_make_engine_explicit_pool_pre_ping_not_overwritten():
    """Batch passing pool_pre_ping=True explicitly → kept (no-op merge)."""
    engine = make_engine(_URL, pool_pre_ping=True)
    assert engine.pool._pre_ping is True
    engine.dispose()


def test_make_engine_explicit_pool_pre_ping_false_respected():
    """An explicit False from a caller must win over the default."""
    engine = make_engine(_URL, pool_pre_ping=False)
    assert engine.pool._pre_ping is False
    engine.dispose()


def test_make_engine_explicit_pool_recycle_respected():
    """An explicit pool_recycle value must win over the default."""
    engine = make_engine(_URL, pool_recycle=600)
    assert engine.pool._recycle == 600
    engine.dispose()
