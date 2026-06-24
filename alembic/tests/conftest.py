"""Conftest for Alembic migration tests.

Uses a dedicated fresh database (ratis_migration_test) to run upgrade/downgrade
in complete isolation. Each test module is responsible for running the specific
migrations it tests.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_MIGRATION_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# collide on the shared migration test DB. The override env var is
# distinct from TEST_DATABASE_URL because Alembic tests want a virgin DB
# every run (no Base.metadata.create_all has touched it).
if "TEST_MIGRATION_URL" in os.environ:
    TEST_MIGRATION_URL = os.environ["TEST_MIGRATION_URL"]
else:
    # Reuse the helper but with a different base DB name so the worktree
    # suffix is computed identically and the DB is auto-created.
    TEST_MIGRATION_URL = resolve_test_database_url(default_db="ratis_migration_test")


@pytest.fixture(scope="session")
def migration_engine():
    """Engine connected to the migration test database.

    The database is wiped (DROP/CREATE SCHEMA) before the session and after.
    """
    engine = create_engine(TEST_MIGRATION_URL)
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    yield engine
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    engine.dispose()


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """Satisfy CI check — migration tests do not use per-test sessions."""
    yield
