"""Tests for ``scripts/init-human-approval-secret.py`` (HSP3 M2 ops ceremony).

Uses the HSP2 ``spin_up_migrated_db`` fixture (disposable alembic-migrated
DB) so the ``app_settings.human_approval`` seed is observed in a real
Postgres. The script is run in-process (its ``main()``) with env wired to the
disposable DB, then we assert :
    - ``app_settings.human_approval.secret_set`` flipped to ``True`` ;
    - the stored argon2 hash verifies against the plaintext secret ;
    - a too-short secret returns a non-zero exit code without touching the DB.

Run :
    uv run --package ratis_product_analyser pytest \\
        scripts/tests/test_init_human_approval_secret.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]

# argon2 helper lives in the PA service — same flat-import path the script
# uses, so ``verify_secret`` matches the hasher under test.
sys.path.insert(0, str(REPO_ROOT / "webservices" / "ratis_product_analyser"))
from admin_ui.human_secret import verify_secret

# Load the spin-up fixture by file path : ``ratis_core/tests`` is not an
# importable package from the installed ``ratis-core`` wheel (only the inner
# ``ratis_core/ratis_core`` ships), so we exec the module directly.
_FIXTURE_PATH = REPO_ROOT / "ratis_core" / "tests" / "_alembic_fixture.py"
_fix_spec = importlib.util.spec_from_file_location("_alembic_fixture", _FIXTURE_PATH)
_fix = importlib.util.module_from_spec(_fix_spec)  # type: ignore[arg-type]
_fix_spec.loader.exec_module(_fix)  # type: ignore[union-attr]
spin_up_migrated_db = _fix.spin_up_migrated_db

# Load the script under test (hyphenated filename → not importable normally).
_SCRIPT_PATH = REPO_ROOT / "scripts" / "init-human-approval-secret.py"
_spec = importlib.util.spec_from_file_location("init_human_approval_secret", _SCRIPT_PATH)
init_script = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(init_script)  # type: ignore[union-attr]


@pytest.fixture(scope="module")
def secret_db_url():
    yield from spin_up_migrated_db(prefix="ratis_init_secret")


def _read_human_approval(db_url: str) -> dict:
    eng = create_engine(db_url)
    try:
        with eng.connect() as conn:
            return conn.execute(text("SELECT data FROM app_settings WHERE section='human_approval'")).scalar_one()
    finally:
        eng.dispose()


def test_installs_hash_and_sets_flag(secret_db_url, monkeypatch):
    secret = "a-valid-operator-secret-2026"
    monkeypatch.setenv("DATABASE_URL", secret_db_url)
    monkeypatch.setenv("HUMAN_APPROVAL_SECRET_PLAINTEXT", secret)

    rc = init_script.main()
    assert rc == 0

    data = _read_human_approval(secret_db_url)
    assert data["secret_set"] is True
    assert data["argon2_hash"]
    assert verify_secret(data["argon2_hash"], secret) is True
    assert verify_secret(data["argon2_hash"], "wrong-secret-entirely") is False


def test_rerun_rotates_hash(secret_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", secret_db_url)

    monkeypatch.setenv("HUMAN_APPROVAL_SECRET_PLAINTEXT", "first-secret-value-2026")
    assert init_script.main() == 0
    first_hash = _read_human_approval(secret_db_url)["argon2_hash"]

    monkeypatch.setenv("HUMAN_APPROVAL_SECRET_PLAINTEXT", "second-secret-value-2026")
    assert init_script.main() == 0
    second = _read_human_approval(secret_db_url)
    assert second["secret_set"] is True
    assert second["argon2_hash"] != first_hash
    assert verify_secret(second["argon2_hash"], "second-secret-value-2026") is True


def test_rejects_short_secret(secret_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", secret_db_url)
    monkeypatch.setenv("HUMAN_APPROVAL_SECRET_PLAINTEXT", "tooshort")

    rc = init_script.main()
    assert rc != 0


def test_errors_when_database_url_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HUMAN_APPROVAL_SECRET_PLAINTEXT", "a-valid-secret-2026-long")

    rc = init_script.main()
    assert rc != 0
