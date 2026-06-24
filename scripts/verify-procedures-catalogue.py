#!/usr/bin/env python3
"""Verify every procedure in db/procedures/ against its manifest.

HSP1 — defense in depth, run by CI on every PR. For each
`db/procedures/support_*.sql` :
    1. The matching `.manifest.toml` must exist.
    2. `verify_procedure(sql, manifest)` must succeed.

Exit code :
    0  — every atom conforms.
    1  — at least one atom is non-conforming or missing its sidecar.
        The first failure's class name and message are printed to stderr.

Usage :
    uv run python scripts/verify-procedures-catalogue.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT / "ratis_core"))

from ratis_core.db_procedure_manifest import (
    ManifestNotFoundError,
    load_manifest,
)
from ratis_core.db_procedure_verifier import (
    VerifierError,
    verify_procedure,
)

PROCEDURES_DIR = REPO_ROOT / "db" / "procedures"


def main() -> int:
    if not PROCEDURES_DIR.exists():
        print(f"ERROR: {PROCEDURES_DIR} does not exist", file=sys.stderr)
        return 1

    sql_files = sorted(p for p in PROCEDURES_DIR.glob("*.sql") if not p.name.startswith("_"))
    if not sql_files:
        print("OK: no atoms to verify (empty catalogue).")
        return 0

    failures: list[str] = []
    for sql_path in sql_files:
        manifest_path = sql_path.with_suffix("").with_suffix(".manifest.toml")
        try:
            manifest = load_manifest(manifest_path)
            verify_procedure(sql_path, manifest)
        except (ManifestNotFoundError, VerifierError) as exc:
            failures.append(f"{sql_path.name}: {type(exc).__name__}: {exc}")

    if failures:
        print("FAILED: non-conforming atoms detected", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"OK: {len(sql_files)} atom(s) conform to their manifests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
