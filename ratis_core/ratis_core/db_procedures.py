"""Alembic helper — apply a support stored procedure from its canonical git source.

A migration that adds or changes a `support_*` procedure calls :

    from ratis_core.db_procedures import apply_procedure

    def upgrade() -> None:
        apply_procedure("support_credit_cab")

`apply_procedure` reads `db/procedures/<name>.sql` (git source of truth) AND
`db/procedures/<name>.manifest.toml` (HSP1 sidecar), validates the manifest,
runs the pglast verifier against the SQL, then executes the SQL via Alembic's
`op.execute`. Procedures are declared `CREATE OR REPLACE`, so the operation is
safe to re-run.

The verifier+manifest pair is HSP1's defense-in-depth : even if a future
commit slips past the CI verifier job, this re-check inside the migration
refuses to apply a non-conforming atom.
"""

from __future__ import annotations

from pathlib import Path

from ratis_core.db_procedure_manifest import load_manifest
from ratis_core.db_procedure_verifier import verify_procedure

# Repo root = three parents up from this file
# (ratis_core/ratis_core/db_procedures.py → ratis_core/ratis_core → ratis_core → repo).
_PROCEDURES_DIR = Path(__file__).resolve().parents[2] / "db" / "procedures"


def _op_execute(sql: str) -> None:
    """Indirection over `alembic.op.execute` — lazy import (op is only valid
    inside a running migration) and a clean monkeypatch point for tests.
    """
    from alembic import op

    op.execute(sql)


def apply_procedure(name: str) -> None:
    """Apply the support procedure `name` from `db/procedures/<name>.sql`.

    HSP1 contract :
        1. Reads `db/procedures/<name>.sql` (existing SP1 behaviour).
        2. Reads `db/procedures/<name>.manifest.toml` (HSP1 — sidecar).
        3. Validates the manifest via Pydantic.
        4. Runs the structural verifier (`pglast`) against the SQL.
        5. Only then : `op.execute(sql)`.

    Raises :
        FileNotFoundError : the `.sql` source is absent.
        ManifestNotFoundError : the `.manifest.toml` sidecar is absent.
        pydantic.ValidationError : the manifest schema rejects the TOML.
        VerifierError (or sub-class) : the SQL diverges from the manifest.
    """
    sql_path = _PROCEDURES_DIR / f"{name}.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"procedure source not found: {sql_path}")

    manifest_path = _PROCEDURES_DIR / f"{name}.manifest.toml"
    manifest = load_manifest(manifest_path)
    verify_procedure(sql_path, manifest)

    _op_execute(sql_path.read_text(encoding="utf-8"))
