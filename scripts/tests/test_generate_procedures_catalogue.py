"""Tests for scripts/generate-procedures-catalogue.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "generate-procedures-catalogue.py"
_spec = importlib.util.spec_from_file_location("gen_procs", _SCRIPT)
gen = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(gen)  # type: ignore[union-attr]


_PROC_SQL = """\
CREATE OR REPLACE PROCEDURE support_credit_cab(
    IN  user_id       bigint,
    IN  amount        integer,
    OUT rows_affected integer
)
LANGUAGE plpgsql AS $$
BEGIN
    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_credit_cab(bigint, integer, integer)
    IS 'Crédite le solde CAB d''un utilisateur.';
"""


def test_parse_procedure_extracts_name_args_comment(tmp_path: Path) -> None:
    f = tmp_path / "support_credit_cab.sql"
    f.write_text(_PROC_SQL, encoding="utf-8")
    parsed = gen.parse_procedure(f)
    assert parsed["name"] == "support_credit_cab"
    assert "user_id" in parsed["args"]
    assert parsed["comment"] == "Crédite le solde CAB d'un utilisateur."


def test_collect_ignores_underscore_prefixed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "support_credit_cab.sql").write_text(_PROC_SQL, encoding="utf-8")
    (tmp_path / "_TEMPLATE.sql").write_text(_PROC_SQL, encoding="utf-8")
    monkeypatch.setattr(gen, "PROCEDURES_DIR", tmp_path)
    procs = gen.collect_procedures()
    assert [p["name"] for p in procs] == ["support_credit_cab"]


def test_generate_empty_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "PROCEDURES_DIR", tmp_path)
    content = gen.generate()
    assert "Aucune procédure support" in content
    assert "Total : 0 procédure" in content


def test_generate_with_procedure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "support_credit_cab.sql").write_text(_PROC_SQL, encoding="utf-8")
    monkeypatch.setattr(gen, "PROCEDURES_DIR", tmp_path)
    content = gen.generate()
    assert "`support_credit_cab`" in content
    assert "Crédite le solde CAB" in content
    assert "Total : 1 procédure" in content


def test_parse_procedure_missing_comment_raises(tmp_path: Path) -> None:
    f = tmp_path / "support_bad.sql"
    f.write_text("CREATE OR REPLACE PROCEDURE support_bad() LANGUAGE plpgsql AS $$ BEGIN END $$;", encoding="utf-8")
    with pytest.raises(ValueError, match="COMMENT"):
        gen.parse_procedure(f)


def test_check_live_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """check_live returns 0 when git and live agree."""
    (tmp_path / "support_credit_cab.sql").write_text(_PROC_SQL, encoding="utf-8")
    monkeypatch.setattr(gen, "PROCEDURES_DIR", tmp_path)
    monkeypatch.setattr(gen, "_run_psql", lambda env, sql: "support_credit_cab\n")
    assert gen.check_live("dev") == 0


def test_check_live_drift_live_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """check_live returns 1 when the live DB has a procedure absent from git."""
    monkeypatch.setattr(gen, "PROCEDURES_DIR", tmp_path)  # empty git dir
    monkeypatch.setattr(gen, "_run_psql", lambda env, sql: "support_rogue\n")
    assert gen.check_live("dev") == 1


def test_check_live_drift_git_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """check_live returns 1 when git has a procedure not yet applied to the live DB."""
    (tmp_path / "support_credit_cab.sql").write_text(_PROC_SQL, encoding="utf-8")
    monkeypatch.setattr(gen, "PROCEDURES_DIR", tmp_path)
    monkeypatch.setattr(gen, "_run_psql", lambda env, sql: "")
    assert gen.check_live("dev") == 1


def test_check_live_unknown_env() -> None:
    """`--check-live` with an unknown env returns exit code 2."""
    assert gen.main(["--check-live", "staging"]) == 2
