"""DA-5 safety guards — refuses to run if ENVIRONMENT=production or
``DATABASE_URL`` doesn't look like a seed/dev DB.

No real DB is touched here — Wave 1 placeholders are no-op so a happy-path
``main()`` invocation just prints and returns.
"""

from __future__ import annotations

import pytest

from scripts.seed.main import _check_safety_guards, main


class TestSafetyGuards:
    def test_production_env_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ENVIRONMENT=production → RuntimeError (DA-5 primary signal)."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed",
        )
        with pytest.raises(RuntimeError, match="production"):
            _check_safety_guards()

    def test_production_env_uppercase_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Casing should not matter — defense-in-depth."""
        monkeypatch.setenv("ENVIRONMENT", "PRODUCTION")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed",
        )
        with pytest.raises(RuntimeError, match="production"):
            _check_safety_guards()

    def test_database_url_unset_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing DATABASE_URL → RuntimeError (cannot even connect)."""
        monkeypatch.setenv("ENVIRONMENT", "seed")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL not set"):
            _check_safety_guards()

    def test_database_url_without_seed_or_dev_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DATABASE_URL must contain '_seed' or '_dev' substring (DA-5 secondary)."""
        monkeypatch.setenv("ENVIRONMENT", "seed")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@prod-host:5432/ratis_prod",
        )
        with pytest.raises(RuntimeError, match="_seed.*_dev|_dev.*_seed|_seed|_dev"):
            _check_safety_guards()

    def test_seed_url_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ENVIRONMENT=seed + DATABASE_URL=…ratis_seed → no exception."""
        monkeypatch.setenv("ENVIRONMENT", "seed")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed",
        )
        # Should not raise.
        _check_safety_guards()

    def test_dev_url_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ENVIRONMENT=dev + DATABASE_URL=…ratis_dev → no exception."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev",
        )
        _check_safety_guards()


class TestMainOrchestrator:
    """Smoke tests for the orchestrator that do NOT touch the DB.

    The Wave-1 happy-path test (placeholders printing only) was replaced by
    ``test_seed_e2e.py`` which spins up a real PG database. The remaining
    tests here cover the pre-DB safety-guard short-circuit path :
    ``main()`` must raise BEFORE creating any session when the env smells
    like production.
    """

    def test_main_aborts_on_production_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() must raise before invoking any seed_* function."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@prod:5432/ratis_seed",
        )
        with pytest.raises(RuntimeError, match="production"):
            main()

    def test_main_aborts_on_unsafe_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DATABASE_URL without _seed/_dev → abort BEFORE the engine init."""
        monkeypatch.setenv("ENVIRONMENT", "seed")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@prod:5432/ratis_prod",
        )
        with pytest.raises(RuntimeError, match="_seed.*_dev|_dev.*_seed|_seed|_dev"):
            main()
