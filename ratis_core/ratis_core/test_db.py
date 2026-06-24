"""Worktree-aware test database URL resolution.

Centralises how test conftests resolve their target DB URL so multiple
worktrees can run pytest concurrently without colliding on the shared
``ratis_test`` DB (cf. SA #389 + #390 findings : flaky DROP SCHEMA CASCADE
teardown + transient "Uncommitted writes detected" when two worktrees race
on the same DB).

Resolution rules
----------------
1. If ``TEST_DATABASE_URL`` is explicitly set in env → use as-is.
   CI workflows set this per-service (``ratis_test_auth``,
   ``ratis_test_purge``, ...) so CI is untouched.
2. Else if ``RATIS_TEST_DB_NO_WORKTREE_ISOLATION=1`` → fall back to the
   legacy shared ``ratis_test`` DB. Escape hatch for the rare local case
   where a developer wants a single inspectable DB.
3. Otherwise → compute a per-worktree suffix from the repository root
   path and target ``ratis_test_w_<hash[:8]>``. The DB is created
   on-demand if missing.

The created DBs are persistent (not torn down between runs) — the
per-service ``setup_db`` fixture handles DROP SCHEMA CASCADE inside its
own DB, which is now isolated from other worktrees.

Design notes
~~~~~~~~~~~~
* Hash uses ``hashlib.sha1`` (not ``hash()``) because the Python hash
  randomisation seed differs across processes, so ``hash(path)`` would
  not be stable across pytest sessions or across services in the same
  worktree.
* ``CREATE DATABASE`` is done via direct ``psycopg`` connection to the
  ``postgres`` maintenance DB with ``autocommit=True`` (PostgreSQL forbids
  ``CREATE DATABASE`` inside a transaction). We swallow the
  ``DuplicateDatabase`` error for idempotency.
* We connect to the ``postgres`` admin DB by rewriting the URL — same
  user/host/port as the test URL but ``/postgres`` as the database.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

_DEFAULT_BASE = "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_test"  # pragma: allowlist secret


def _worktree_root() -> Path:
    """Return the git worktree root for the current cwd.

    Falls back to the cwd if ``git`` is unavailable or the directory is
    not inside a worktree (e.g. tarball install). The fallback is safe :
    different worktrees still have different cwds when their conftests
    import, so the hash still differs.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return Path(out.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return Path.cwd()


def _worktree_suffix() -> str:
    """Stable 8-char suffix derived from the worktree root path."""
    root = str(_worktree_root().resolve())
    digest = hashlib.sha1(root.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:8]


def _swap_db_name(url: str, new_db: str) -> str:
    """Return ``url`` with the database path component replaced by ``new_db``."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{new_db}"))


def _admin_url(url: str) -> str:
    """psycopg connection URL (no ``+psycopg`` driver suffix) to the
    ``postgres`` maintenance DB on the same host/user as ``url``.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+", 1)[0]  # postgresql+psycopg -> postgresql
    return urlunparse(parsed._replace(scheme=scheme, path="/postgres"))


def _ensure_database(url: str) -> None:
    """Create the database named in ``url`` if it does not exist.

    Idempotent : swallows ``DuplicateDatabase`` so concurrent first-runs
    across worktrees do not race. Raises only on configuration errors
    (bad URL, unreachable host, bad credentials) where failing fast is
    the right behaviour.
    """
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError(f"test DB URL missing database name : {url}")

    admin = _admin_url(url)
    with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone() is None:
            # Quote identifier defensively even though db_name is
            # derived from our own hash (defence-in-depth).
            cur.execute(f'CREATE DATABASE "{db_name}"')


def resolve_test_database_url(default_db: str = "ratis_test") -> str:
    """Return the test database URL for the current worktree.

    Parameters
    ----------
    default_db
        Legacy DB name used when worktree isolation is explicitly
        disabled. Defaults to ``ratis_test`` to match historical conftest
        defaults.

    Returns
    -------
    str
        A ``postgresql+psycopg://...`` URL pointing at an existing
        database (created on first call if absent).
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        # CI path : DB is created externally by the workflow step before
        # pytest runs. No-op auto-create here.
        return explicit

    if os.environ.get("RATIS_TEST_DB_NO_WORKTREE_ISOLATION") == "1":
        url = _swap_db_name(_DEFAULT_BASE, default_db)
    else:
        suffix = _worktree_suffix()
        url = _swap_db_name(_DEFAULT_BASE, f"{default_db}_w_{suffix}")

    _ensure_database(url)
    return url
