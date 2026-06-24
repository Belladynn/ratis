"""Pytest helper : spin up a disposable Postgres DB and run
``alembic upgrade heads`` against it.

Used by HSP2 tests that need to observe **PG-side artefacts** (triggers,
functions, partial indexes) not reproduced by ``Base.metadata.create_all()``.

The fixture is a function, not a pytest fixture — callers decorate it with
``@pytest.fixture(scope="module")``. This lets each test module choose its
own scope while sharing the spin-up logic.

Pattern lifted from ``test_schema_sync.py`` (HSP1-era code) — kept in a
separate module so HSP2 and future HSPs can reuse it without circular
imports between test files.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ADMIN_URL = "postgresql+psycopg://ratis:ratis@localhost:5432/postgres"  # pragma: allowlist secret


def spin_up_migrated_db(prefix: str = "ratis_hsp2") -> Iterator[str]:
    """Yield the URL of a freshly created Postgres DB at ``alembic upgrade heads``.

    The DB is dropped on teardown — best effort, never raises.

    Skips the calling test if Postgres or the alembic CLI is unreachable.
    """
    db_name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    fresh_url = f"postgresql+psycopg://ratis:ratis@localhost:5432/{db_name}"  # pragma: allowlist secret

    try:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            conn.execute(text(f"CREATE DATABASE {db_name}"))
        admin.dispose()
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable for HSP2 alembic-spinup: {exc}")

    env = os.environ.copy()
    env["DATABASE_URL"] = fresh_url
    try:
        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "heads"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _drop(db_name)
        pytest.skip(f"alembic CLI unavailable for HSP2 spinup: {exc}")

    if result.returncode != 0:
        _drop(db_name)
        pytest.fail(
            f"alembic upgrade heads failed during HSP2 spinup:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    try:
        yield fresh_url
    finally:
        _drop(db_name)


def _drop(db_name: str) -> None:
    """Best-effort DB drop. Used on teardown ; never raises."""
    try:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": db_name},
            )
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        admin.dispose()
    except OperationalError:
        pass
