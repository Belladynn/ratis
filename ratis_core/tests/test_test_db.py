"""Tests for ratis_core.test_db worktree-aware URL resolution.

These tests are intentionally narrow on the DB side : they validate URL
shape and the env-driven branches without actually creating Postgres
databases. The ``_ensure_database`` side effect is monkeypatched out.
"""

from __future__ import annotations

from unittest import mock

import pytest

from ratis_core import test_db as td


def test_explicit_env_var_wins(monkeypatch):
    """If TEST_DATABASE_URL is set, it is returned untouched (CI path)."""
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://x:y@h/explicit_db")
    monkeypatch.delenv("RATIS_TEST_DB_NO_WORKTREE_ISOLATION", raising=False)
    # No DB creation must happen — we'd hit the network otherwise.
    with mock.patch.object(td, "_ensure_database") as ensure:
        assert td.resolve_test_database_url() == "postgresql+psycopg://x:y@h/explicit_db"
        ensure.assert_not_called()


def test_no_worktree_isolation_returns_legacy_default(monkeypatch):
    """Escape hatch : developer can opt back into the shared ratis_test."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("RATIS_TEST_DB_NO_WORKTREE_ISOLATION", "1")
    with mock.patch.object(td, "_ensure_database") as ensure:
        url = td.resolve_test_database_url()
        assert url.endswith("/ratis_test")
        ensure.assert_called_once_with(url)


def test_default_path_uses_worktree_suffix(monkeypatch):
    """Default branch : DB name carries a stable worktree-derived suffix."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("RATIS_TEST_DB_NO_WORKTREE_ISOLATION", raising=False)
    with mock.patch.object(td, "_ensure_database") as ensure:
        url = td.resolve_test_database_url()
        # ratis_test_w_<8 hex chars>
        path = url.rsplit("/", 1)[-1]
        assert path.startswith("ratis_test_w_")
        assert len(path) == len("ratis_test_w_") + 8
        # hex chars only
        suffix = path[len("ratis_test_w_") :]
        int(suffix, 16)  # raises ValueError if non-hex
        ensure.assert_called_once_with(url)


def test_suffix_is_stable_for_same_worktree(monkeypatch):
    """Calling twice from the same cwd returns the same DB name."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("RATIS_TEST_DB_NO_WORKTREE_ISOLATION", raising=False)
    with mock.patch.object(td, "_ensure_database"):
        a = td.resolve_test_database_url()
        b = td.resolve_test_database_url()
        assert a == b


def test_different_worktrees_get_different_dbs(monkeypatch, tmp_path):
    """Two distinct repo roots yield distinct DB suffixes."""
    fake_a = tmp_path / "worktree_a"
    fake_b = tmp_path / "worktree_b"
    fake_a.mkdir()
    fake_b.mkdir()

    def make_fake_root(path):
        def _fn():
            return path

        return _fn

    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("RATIS_TEST_DB_NO_WORKTREE_ISOLATION", raising=False)

    with mock.patch.object(td, "_ensure_database"):
        with mock.patch.object(td, "_worktree_root", make_fake_root(fake_a)):
            url_a = td.resolve_test_database_url()
        with mock.patch.object(td, "_worktree_root", make_fake_root(fake_b)):
            url_b = td.resolve_test_database_url()

    assert url_a != url_b
    assert url_a.rsplit("/", 1)[-1].startswith("ratis_test_w_")
    assert url_b.rsplit("/", 1)[-1].startswith("ratis_test_w_")


def test_swap_db_name_preserves_user_host_port():
    """URL surgery keeps credentials/host/port intact."""
    out = td._swap_db_name("postgresql+psycopg://u:p@h:1234/old", "new_db")
    assert out == "postgresql+psycopg://u:p@h:1234/new_db"


def test_admin_url_strips_driver_and_targets_postgres():
    """psycopg can't load SQLAlchemy URL with +psycopg suffix."""
    out = td._admin_url("postgresql+psycopg://u:p@h:1234/ratis_test")
    assert out == "postgresql://u:p@h:1234/postgres"


def test_worktree_suffix_is_8_hex_chars(monkeypatch, tmp_path):
    """Suffix shape is locked : exactly 8 hex chars."""
    monkeypatch.chdir(tmp_path)
    suffix = td._worktree_suffix()
    assert len(suffix) == 8
    int(suffix, 16)


def test_worktree_root_falls_back_to_cwd_outside_git(monkeypatch, tmp_path):
    """When git is unreachable, the helper does not raise — cwd is used."""
    monkeypatch.chdir(tmp_path)

    def _fake_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    with mock.patch("subprocess.run", _fake_run):
        root = td._worktree_root()
        assert root == tmp_path or root == tmp_path.resolve()


def test_ensure_database_rejects_url_without_db_name():
    """Configuration mistake fails fast rather than connecting to postgres DB."""
    with pytest.raises(ValueError, match="missing database name"):
        td._ensure_database("postgresql+psycopg://u:p@h:1234/")
