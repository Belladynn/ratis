"""Tests for the pglast-based procedure verifier.

Each verifier check (8 total) has both a positive (valid SQL passes) and
a negative (broken SQL is rejected with the expected error class) test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ratis_core.db_procedure_manifest import ProcedureManifest
from ratis_core.db_procedure_verifier import (
    BodyMissingDiagnosticsError,
    CommentMissingError,
    DynamicExecuteForbiddenError,
    NameMismatchError,
    OutRowsAffectedMissingError,
    SignatureMismatchError,
    SqlParseError,
    TableNotDeclaredError,
    VerifierError,
    verify_procedure,
)

# ----------------------------------------------------------------------
# Helpers : write a (.sql, manifest) pair to tmp_path and return paths.
# ----------------------------------------------------------------------


def _manifest_credit() -> ProcedureManifest:
    """Canonical valid manifest used by most positive tests."""
    return ProcedureManifest(
        name="support_credit_cab",
        purpose="Ajouter des CAB a un user.",
        facing=True,
        direction="credit",
        money_tier="cab",
        args=[
            {"name": "p_user_id", "type": "uuid", "required": True},
            {"name": "p_amount", "type": "integer", "required": True, "min": 1, "max": 10000},
        ],
        affects=[
            {"table": "user_cab_balance", "op": "update", "rows": 1, "columns": ["balance"]},
            {"table": "cabecoin_transactions", "op": "insert", "rows": 1},
        ],
    )


_VALID_SQL = """\
CREATE OR REPLACE PROCEDURE support_credit_cab(
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

    INSERT INTO cabecoin_transactions (user_id, direction, amount, reason)
    VALUES (p_user_id, 'credit', p_amount, 'admin_adjustment');

    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_credit_cab(uuid, integer, integer)
    IS 'Credit support : ajoute N CAB au user.';
"""


def _write(tmp_path: Path, sql: str) -> Path:
    p = tmp_path / "atom.sql"
    p.write_text(sql, encoding="utf-8")
    return p


# ----------------------------------------------------------------------
# Check 0 — happy path : valid SQL + valid manifest passes silently.
# ----------------------------------------------------------------------


def test_verify_accepts_conforming_procedure(tmp_path: Path) -> None:
    """Returns None on success — no exception raised."""
    sql_path = _write(tmp_path, _VALID_SQL)
    assert verify_procedure(sql_path, _manifest_credit()) is None


# ----------------------------------------------------------------------
# Check 1 — SQL parses via pglast, else SqlParseError.
# ----------------------------------------------------------------------


def test_verify_rejects_unparseable_sql(tmp_path: Path) -> None:
    sql_path = _write(tmp_path, "this is not SQL at all $%@!")
    with pytest.raises(SqlParseError, match="parse"):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 2 — CREATE PROCEDURE name matches manifest.name.
# ----------------------------------------------------------------------


def test_verify_rejects_name_mismatch(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace("support_credit_cab", "support_typo_cab", 1)
    # Restore the COMMENT line so we hit name-mismatch on the CREATE, not on
    # missing comment for the manifest name.
    bad = bad.replace(
        "COMMENT ON PROCEDURE support_credit_cab",
        "COMMENT ON PROCEDURE support_typo_cab",
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(NameMismatchError, match="support_typo_cab"):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 3 — signature (IN params, in order, types loose-matched).
# ----------------------------------------------------------------------


def test_verify_rejects_extra_in_param(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace(
        "IN  p_amount       integer,",
        "IN  p_amount       integer,\n    IN  p_extra        text,",
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(SignatureMismatchError):
        verify_procedure(sql_path, _manifest_credit())


def test_verify_rejects_wrong_in_param_type(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace(
        "IN  p_user_id      uuid,",
        "IN  p_user_id      bigint,",
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(SignatureMismatchError, match="p_user_id"):
        verify_procedure(sql_path, _manifest_credit())


def test_verify_rejects_wrong_in_param_name(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace(
        "IN  p_user_id      uuid,",
        "IN  p_other_id     uuid,",
    )
    # Update the body so it still parses semantically — we want
    # SignatureMismatchError, not a side-effect.
    bad = bad.replace("p_user_id", "p_other_id")
    sql_path = _write(tmp_path, bad)
    with pytest.raises(SignatureMismatchError):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 4 — `OUT rows_affected integer` present.
# ----------------------------------------------------------------------


def test_verify_rejects_missing_out_rows_affected(tmp_path: Path) -> None:
    bad = (
        _VALID_SQL.replace(
            "    OUT rows_affected  integer\n",
            "",
        )
        .replace(
            "    IN  p_amount       integer,\n",
            "    IN  p_amount       integer\n",
        )
        .replace(
            "    GET DIAGNOSTICS rows_affected = ROW_COUNT;\n",
            "",
        )
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(OutRowsAffectedMissingError):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 5 — body contains GET DIAGNOSTICS rows_affected = ROW_COUNT.
# ----------------------------------------------------------------------


def test_verify_rejects_missing_get_diagnostics(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace(
        "    GET DIAGNOSTICS rows_affected = ROW_COUNT;\n",
        "",
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(BodyMissingDiagnosticsError):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 6 — COMMENT ON PROCEDURE <name> IS '...' present.
# ----------------------------------------------------------------------


def test_verify_rejects_missing_comment(tmp_path: Path) -> None:
    bad = _VALID_SQL.split("COMMENT ON PROCEDURE")[0]
    sql_path = _write(tmp_path, bad)
    with pytest.raises(CommentMissingError):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 7 — tables touched by DML ⊆ {a.table for a in affects}.
# ----------------------------------------------------------------------


def test_verify_rejects_undeclared_table(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace(
        "    INSERT INTO cabecoin_transactions (user_id, direction, amount, reason)\n"
        "    VALUES (p_user_id, 'credit', p_amount, 'admin_adjustment');\n",
        "    INSERT INTO secret_audit_log (user_id, note)\n    VALUES (p_user_id, 'sneaky');\n",
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(TableNotDeclaredError, match="secret_audit_log"):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# Check 8 — no dynamic EXECUTE.
# ----------------------------------------------------------------------


def test_verify_rejects_dynamic_execute(tmp_path: Path) -> None:
    bad = _VALID_SQL.replace(
        "    GET DIAGNOSTICS rows_affected = ROW_COUNT;\n",
        "    EXECUTE 'UPDATE user_cab_balance SET balance = balance';\n"
        "    GET DIAGNOSTICS rows_affected = ROW_COUNT;\n",
    )
    sql_path = _write(tmp_path, bad)
    with pytest.raises(DynamicExecuteForbiddenError):
        verify_procedure(sql_path, _manifest_credit())


# ----------------------------------------------------------------------
# All error classes are sub-classes of VerifierError.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [
        SqlParseError,
        NameMismatchError,
        SignatureMismatchError,
        OutRowsAffectedMissingError,
        BodyMissingDiagnosticsError,
        CommentMissingError,
        TableNotDeclaredError,
        DynamicExecuteForbiddenError,
    ],
)
def test_all_errors_inherit_verifier_error(cls: type) -> None:
    assert issubclass(cls, VerifierError)
