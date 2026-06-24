"""Tests for the Alembic procedure-application helper."""

from __future__ import annotations

from pathlib import Path

import pytest
from ratis_core.db_procedure_manifest import ManifestNotFoundError
from ratis_core.db_procedure_verifier import TableNotDeclaredError

from ratis_core import db_procedures

# --- Canonical conforming atom — used by tests that need a valid pair ---

_ATOM_SQL = """\
CREATE OR REPLACE PROCEDURE support_demo(
    IN  p_user_id      uuid,
    IN  p_amount       integer,
    OUT rows_affected  integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE user_cab_balance
    SET balance = balance + p_amount
    WHERE user_id = p_user_id;

    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_demo(uuid, integer, integer)
    IS 'Demo atom pour tests.';
"""

_ATOM_MANIFEST = """\
name        = "support_demo"
purpose     = "Demo atom pour tests."
facing      = true
direction   = "credit"
money_tier  = "cab"

[[args]]
name      = "p_user_id"
type      = "uuid"
required  = true

[[args]]
name      = "p_amount"
type      = "integer"
required  = true
min       = 1
max       = 10000

[[affects]]
table   = "user_cab_balance"
op      = "update"
rows    = 1
"""


def _install_atom(proc_dir: Path, name: str, sql: str, manifest: str) -> None:
    """Write the .sql and .manifest.toml pair into proc_dir."""
    (proc_dir / f"{name}.sql").write_text(sql, encoding="utf-8")
    (proc_dir / f"{name}.manifest.toml").write_text(manifest, encoding="utf-8")


# ----------------------------------------------------------------------
# Existing SP1 contracts — must still pass under HSP1.
# ----------------------------------------------------------------------


def test_apply_procedure_executes_file_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`apply_procedure` reads db/procedures/<name>.sql and feeds it to op.execute.

    HSP1 extension : a conforming `.manifest.toml` sidecar is now also required ;
    the SP1-era test is updated to install both files.
    """
    proc_dir = tmp_path / "db" / "procedures"
    proc_dir.mkdir(parents=True)
    _install_atom(proc_dir, "support_demo", _ATOM_SQL, _ATOM_MANIFEST)
    monkeypatch.setattr(db_procedures, "_PROCEDURES_DIR", proc_dir)

    executed: list[str] = []
    monkeypatch.setattr(db_procedures, "_op_execute", lambda sql: executed.append(sql))

    db_procedures.apply_procedure("support_demo")

    assert executed == [_ATOM_SQL]


def test_apply_procedure_missing_file_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing procedure source raises FileNotFoundError (fail loud in the migration)."""
    proc_dir = tmp_path / "db" / "procedures"
    proc_dir.mkdir(parents=True)
    monkeypatch.setattr(db_procedures, "_PROCEDURES_DIR", proc_dir)
    monkeypatch.setattr(db_procedures, "_op_execute", lambda sql: None)

    with pytest.raises(FileNotFoundError, match="support_absent"):
        db_procedures.apply_procedure("support_absent")


# ----------------------------------------------------------------------
# HSP1 — new contracts.
# ----------------------------------------------------------------------


def test_apply_procedure_refuses_when_manifest_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SQL present but `.manifest.toml` absent -> ManifestNotFoundError, no execute."""
    proc_dir = tmp_path / "db" / "procedures"
    proc_dir.mkdir(parents=True)
    (proc_dir / "support_demo.sql").write_text(_ATOM_SQL, encoding="utf-8")
    monkeypatch.setattr(db_procedures, "_PROCEDURES_DIR", proc_dir)

    executed: list[str] = []
    monkeypatch.setattr(db_procedures, "_op_execute", lambda sql: executed.append(sql))

    with pytest.raises(ManifestNotFoundError, match="support_demo"):
        db_procedures.apply_procedure("support_demo")
    assert executed == []  # defense in depth — nothing applied.


def test_apply_procedure_refuses_when_verifier_rejects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SQL touches an undeclared table -> TableNotDeclaredError, no execute."""
    proc_dir = tmp_path / "db" / "procedures"
    proc_dir.mkdir(parents=True)
    bad_sql = _ATOM_SQL.replace(
        "    UPDATE user_cab_balance\n    SET balance = balance + p_amount\n    WHERE user_id = p_user_id;\n",
        "    UPDATE user_cab_balance\n    SET balance = balance + p_amount\n    WHERE user_id = p_user_id;\n"
        "    INSERT INTO secret_table (note) VALUES ('boom');\n",
    )
    _install_atom(proc_dir, "support_demo", bad_sql, _ATOM_MANIFEST)
    monkeypatch.setattr(db_procedures, "_PROCEDURES_DIR", proc_dir)

    executed: list[str] = []
    monkeypatch.setattr(db_procedures, "_op_execute", lambda sql: executed.append(sql))

    with pytest.raises(TableNotDeclaredError, match="secret_table"):
        db_procedures.apply_procedure("support_demo")
    assert executed == []


def test_apply_procedure_runs_verifier_before_execute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Defense-in-depth : verifier runs BEFORE op.execute, even if both would pass.

    Asserts the call order : load_manifest -> verify_procedure -> op.execute.
    """
    proc_dir = tmp_path / "db" / "procedures"
    proc_dir.mkdir(parents=True)
    _install_atom(proc_dir, "support_demo", _ATOM_SQL, _ATOM_MANIFEST)
    monkeypatch.setattr(db_procedures, "_PROCEDURES_DIR", proc_dir)

    calls: list[str] = []
    real_load = db_procedures.load_manifest
    real_verify = db_procedures.verify_procedure

    def spy_load(p):
        calls.append("load")
        return real_load(p)

    def spy_verify(p, m):
        calls.append("verify")
        return real_verify(p, m)

    monkeypatch.setattr(db_procedures, "load_manifest", spy_load)
    monkeypatch.setattr(db_procedures, "verify_procedure", spy_verify)
    monkeypatch.setattr(db_procedures, "_op_execute", lambda sql: calls.append("execute"))

    db_procedures.apply_procedure("support_demo")

    assert calls == ["load", "verify", "execute"]
