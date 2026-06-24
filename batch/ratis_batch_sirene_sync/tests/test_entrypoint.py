"""Smoke tests — ratis_batch_sirene_sync entrypoint (PR6 real pipeline).

Tests run the entrypoint as a subprocess to verify argument parsing, env
validation (require_env), and clean startup/exit.  The heavy pipeline is
mocked via monkeypatching so no DB or network I/O happens.

Note: PR3 tests checked for 'stub' in stderr — that text no longer exists
now that the full pipeline is wired (PR6). Tests updated accordingly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the entrypoint script relative to the batch package directory.
SIRENE_SCRIPT = str(Path(__file__).parent.parent / "sirene_sync.py")

# Env required by require_env() at startup.
_BASE_ENV = {
    "DATABASE_URL": "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev",  # pragma: allowlist secret
    "SIRENE_BULK_URL": "https://files.data.gouv.fr/insee-sirene/StockEtablissement_utf8.zip",
    "GEOPLATEFORME_GEOCODE_URL": "https://data.geopf.fr/geocodage",
    "SIRENE_BULK_CACHE_DIR": "/tmp/ratis-sirene-test",
}


def test_entrypoint_missing_env_exits_nonzero():
    """Missing required env vars -> RuntimeError -> non-zero exit code."""
    result = subprocess.run(
        [sys.executable, SIRENE_SCRIPT, "--dry-run"],
        capture_output=True,
        text=True,
        env={},  # no env vars
    )
    assert result.returncode != 0, f"Expected non-zero exit when env vars missing, got 0.\nstderr: {result.stderr}"


def test_entrypoint_env_validation_message():
    """Missing DATABASE_URL -> error message mentions the missing var."""
    env_without_db = {k: v for k, v in _BASE_ENV.items() if k != "DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, SIRENE_SCRIPT, "--dry-run"],
        capture_output=True,
        text=True,
        env=env_without_db,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "DATABASE_URL" in combined, f"Expected DATABASE_URL in error output.\ncombined: {combined}"


def test_entrypoint_help_exits_zero():
    """--help must print usage and exit 0 (argparse convention)."""
    result = subprocess.run(
        [sys.executable, SIRENE_SCRIPT, "--help"],
        capture_output=True,
        text=True,
        env=_BASE_ENV,
    )
    assert result.returncode == 0, f"Expected exit 0 for --help, got {result.returncode}.\nstderr: {result.stderr}"
    assert "dry-run" in result.stdout.lower() or "usage" in result.stdout.lower(), (
        f"Expected usage info in stdout.\nstdout: {result.stdout}"
    )
