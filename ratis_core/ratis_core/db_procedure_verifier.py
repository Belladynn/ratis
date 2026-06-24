"""Structural verifier — confronts a stored-procedure `.sql` to its manifest.

HSP1 — defense layer ④ : un atome n'est cataloguable que si le parseur
Postgres (`pglast`, binding `libpg_query`) confirme que son corps fait
exactement ce que declare son manifeste sidecar. Le verifier tourne
(a) en CI sur tout le catalogue ; (b) dans `apply_procedure`, juste
avant `op.execute`.

Aucune dependance hors `pglast` + `ratis_core.db_procedure_manifest`.

Notes d'implementation (pglast v7.13) :
    - `pglast.parser.parse_sql(sql)` retourne un tuple de RawStmt.
    - `CREATE OR REPLACE PROCEDURE` => RawStmt.stmt de type
      `pglast.ast.CreateFunctionStmt` avec `is_procedure=True`.
    - `CreateFunctionStmt.funcname` = tuple de `pglast.ast.String` (.sval).
    - `CreateFunctionStmt.parameters` = tuple de `FunctionParameter`.
      `.mode` est de type `pglast.enums.FunctionParameterMode`
      (valeurs : FUNC_PARAM_IN='i', FUNC_PARAM_OUT='o').
    - `COMMENT ON PROCEDURE` => `pglast.ast.CommentStmt` avec
      `.objtype == pglast.enums.ObjectType.OBJECT_PROCEDURE` (valeur 29).
    - `pglast.parse_plpgsql(sql)` (module-level, PAS pglast.parser) retourne
      `list[dict]` (JSON brut) ; structure :
      [{"PLpgSQL_function": {"action": {"PLpgSQL_stmt_block": {"body": [...]}}}}]
    - Type normalisation : pglast emet `pg_catalog.int4` pour `integer`,
      `uuid` sans schema, etc. Cf `_norm_type()`.
"""

from __future__ import annotations

from pathlib import Path

import pglast
from pglast import ast, parser
from pglast.enums import FunctionParameterMode, ObjectType

from ratis_core.db_procedure_manifest import ProcedureManifest

# ----------------------------------------------------------------------
# Exception hierarchy — one class per rejection reason, all sub-classes
# of VerifierError. `apply_procedure` only needs to catch the base.
# ----------------------------------------------------------------------


class VerifierError(Exception):
    """Base class — all verifier rejections derive from this."""


class SqlParseError(VerifierError):
    """`pglast.parser.parse_sql` raised — the file is not valid SQL."""


class NameMismatchError(VerifierError):
    """`CREATE PROCEDURE <name>` does not match `manifest.name`."""


class SignatureMismatchError(VerifierError):
    """`IN` params (names / order / types) do not match `manifest.args`."""


class OutRowsAffectedMissingError(VerifierError):
    """Signature lacks `OUT rows_affected integer`."""


class BodyMissingDiagnosticsError(VerifierError):
    """Body lacks `GET DIAGNOSTICS rows_affected = ROW_COUNT`."""


class CommentMissingError(VerifierError):
    """No `COMMENT ON PROCEDURE <name> IS '...'` found."""


class TableNotDeclaredError(VerifierError):
    """A DML statement touches a table absent from `manifest.affects`."""


class DynamicExecuteForbiddenError(VerifierError):
    """Body contains a dynamic `EXECUTE` (PL/pgSQL stmt_dynexecute)."""


# ----------------------------------------------------------------------
# Type normalisation — pglast emits `pg_catalog.int4`, `pg_catalog.int8`,
# `uuid`, `text`, ... ; the manifest writes `integer`, `bigint`, etc.
# We compare the *normalised* forms.
# ----------------------------------------------------------------------


_TYPE_ALIASES: dict[str, str] = {
    "integer": "int4",
    "int": "int4",
    "int4": "int4",
    "bigint": "int8",
    "int8": "int8",
    "smallint": "int2",
    "int2": "int2",
    "text": "text",
    "uuid": "uuid",
    "boolean": "bool",
    "bool": "bool",
    "numeric": "numeric",
    "timestamptz": "timestamptz",
    "timestamp with time zone": "timestamptz",
    "character varying": "varchar",
    "double precision": "float8",
    "real": "float4",
}


def _norm_type(t: str) -> str:
    """Return the canonical Postgres type name (no schema, alias-folded)."""
    bare = t.strip().lower()
    if bare.startswith("pg_catalog."):
        bare = bare[len("pg_catalog.") :]
    return _TYPE_ALIASES.get(bare, bare)


def _typename_to_str(typename: ast.TypeName) -> str:
    """Extract the loose type string from a pglast TypeName node."""
    parts = [n.sval for n in typename.names]
    return _norm_type(".".join(parts))


# ----------------------------------------------------------------------
# AST walkers — find the CreateFunctionStmt + the CommentStmt in the
# raw-statement list, find DML tables in the PL/pgSQL body, detect
# GET DIAGNOSTICS / EXECUTE.
# ----------------------------------------------------------------------


def _find_create_procedure(raw_stmts: tuple) -> ast.CreateFunctionStmt:
    """Return the `CREATE OR REPLACE PROCEDURE` node (with `is_procedure=True`)."""
    for rs in raw_stmts:
        stmt = rs.stmt
        if isinstance(stmt, ast.CreateFunctionStmt) and stmt.is_procedure:
            return stmt
    raise NameMismatchError("no CREATE PROCEDURE found in file")


def _find_comment_on_procedure(raw_stmts: tuple) -> ast.CommentStmt | None:
    """Return the `COMMENT ON PROCEDURE ...` node if present, else None.

    Note: ObjectType is in pglast.enums (not pglast.ast).
    """
    for rs in raw_stmts:
        if isinstance(rs.stmt, ast.CommentStmt) and rs.stmt.objtype == ObjectType.OBJECT_PROCEDURE:
            return rs.stmt
    return None


def _walk_dml_tables_in_query(query: str, touched: set[str]) -> None:
    """Re-parse one DML expression (extracted from PL/pgSQL stmt_execsql) and
    record every relation name targeted by INSERT/UPDATE/DELETE.

    Pglast wraps DML in `RawStmt.stmt` of type `InsertStmt`, `UpdateStmt`,
    or `DeleteStmt` ; each has `.relation` of type `RangeVar` with `.relname`.

    Known blind spot (TODO HSP1.1) : writable-CTE DML tables are NOT tracked.
    A statement like ``WITH x AS (DELETE FROM secret_table RETURNING id)
    INSERT INTO declared_table SELECT * FROM x`` only registers
    ``declared_table`` ; ``secret_table`` is invisible because only the
    top-level statement's ``.relation`` is inspected. Same risk class as the
    CALL-transitive gap documented in ``_walk_plpgsql_body``.
    """
    try:
        sub = parser.parse_sql(query)
    except Exception:
        # If a body expression doesn't parse standalone, ignore — it's
        # likely a SELECT/assignment, not DML. The dynamic-execute check
        # handles the real risky case.
        return
    for rs in sub:
        s = rs.stmt
        if isinstance(s, (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt)) and s.relation is not None:
            touched.add(s.relation.relname)


def _walk_plpgsql_body(
    plpgsql_tree: list[dict],
) -> tuple[set[str], bool, bool]:
    """Walk the PL/pgSQL JSON tree.

    Returns :
        (touched_tables, has_get_diagnostics_rowcount, has_dynexecute)

    Note : la closure transitive sur CALL vers d'autres procedures du
    catalogue n'est PAS implementee (hors-scope HSP1). Si un atome CALL
    un autre atome, ses tables transitives ne sont pas inferees.
    TODO HSP1.1 : implementer l'inference transitive via PLpgSQL_stmt_call.
    """
    touched: set[str] = set()
    has_diag = False
    has_dyn = False

    def visit(node):
        nonlocal has_diag, has_dyn
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        for key, val in node.items():
            if key == "PLpgSQL_stmt_execsql":
                expr = (val.get("sqlstmt") or {}).get("PLpgSQL_expr") or {}
                q = expr.get("query")
                if q:
                    _walk_dml_tables_in_query(q, touched)
            elif key == "PLpgSQL_stmt_dynexecute":
                has_dyn = True
            elif key == "PLpgSQL_stmt_getdiag":
                for di in val.get("diag_items", []):
                    item = di.get("PLpgSQL_diag_item") or {}
                    if item.get("kind") == "ROW_COUNT":
                        has_diag = True
            visit(val)

    visit(plpgsql_tree)
    return touched, has_diag, has_dyn


# ----------------------------------------------------------------------
# Public API.
# ----------------------------------------------------------------------


def verify_procedure(sql_path: Path, manifest: ProcedureManifest) -> None:
    """Verify that the SQL file at `sql_path` conforms to `manifest`.

    Raises the first applicable sub-class of VerifierError on failure ;
    returns None on success. Order of checks :
        1. parse_sql succeeds (SqlParseError)
        2. name matches (NameMismatchError)
        3. OUT rows_affected integer present (OutRowsAffectedMissingError)
        4. IN signature matches manifest.args (SignatureMismatchError)
        5. body has GET DIAGNOSTICS rows_affected = ROW_COUNT (BodyMissingDiagnosticsError)
        6. no dynamic EXECUTE (DynamicExecuteForbiddenError)
        7. tables touched ⊆ declared (TableNotDeclaredError)
        8. COMMENT ON PROCEDURE present (CommentMissingError)

    Note : `pglast.parse_plpgsql` est la fonction module-level (top-level),
    PAS `pglast.parser.parse_plpgsql` (qui n'existe pas en v7.13).
    """
    sql = sql_path.read_text(encoding="utf-8")

    # --- 1. parse the file ---
    try:
        raw_stmts = parser.parse_sql(sql)
    except pglast.parser.ParseError as e:
        raise SqlParseError(f"SQL ne parse pas: {e}") from e

    create = _find_create_procedure(raw_stmts)

    # --- 2. name ---
    sql_name = ".".join(n.sval for n in create.funcname)
    if sql_name != manifest.name:
        raise NameMismatchError(f"nom mismatch: SQL={sql_name!r} manifest={manifest.name!r}")

    # --- 3. signature : IN args + OUT rows_affected integer ---
    # Note : parameters declared *without* an explicit `IN` keyword emit
    # FUNC_PARAM_DEFAULT from pglast (not FUNC_PARAM_IN). Such params are
    # excluded by this filter, making `in_params` appear empty and triggering
    # SignatureMismatchError with count 0. Always use explicit `IN` in atoms.
    in_params = [p for p in (create.parameters or ()) if p.mode == FunctionParameterMode.FUNC_PARAM_IN]
    out_params = [p for p in (create.parameters or ()) if p.mode == FunctionParameterMode.FUNC_PARAM_OUT]

    # OUT rows_affected integer must exist.
    out_ok = any(p.name == "rows_affected" and _typename_to_str(p.argType) == "int4" for p in out_params)
    if not out_ok:
        raise OutRowsAffectedMissingError("OUT rows_affected integer manquant dans la signature")

    # IN params must match manifest.args : same count, same order,
    # same names, same normalised types.
    if len(in_params) != len(manifest.args):
        raise SignatureMismatchError(
            f"signature mismatch: SQL a {len(in_params)} IN params, manifest declare {len(manifest.args)}"
        )
    for sql_p, m_arg in zip(in_params, manifest.args, strict=False):
        if sql_p.name != m_arg.name:
            raise SignatureMismatchError(f"signature mismatch: nom IN SQL={sql_p.name!r} manifest={m_arg.name!r}")
        sql_type = _typename_to_str(sql_p.argType)
        manifest_type = _norm_type(m_arg.type)
        if sql_type != manifest_type:
            raise SignatureMismatchError(
                f"signature mismatch: type de {sql_p.name!r} SQL={sql_type!r} manifest={manifest_type!r}"
            )

    # --- 5, 6, 7 : walk the PL/pgSQL body ---
    # Note: pglast.parse_plpgsql is at module level, NOT pglast.parser.parse_plpgsql
    try:
        plpgsql_tree = pglast.parse_plpgsql(sql)
    except pglast.parser.ParseError as e:
        raise SqlParseError(f"PL/pgSQL ne parse pas: {e}") from e

    touched, has_diag, has_dyn = _walk_plpgsql_body(plpgsql_tree)

    if not has_diag:
        raise BodyMissingDiagnosticsError("GET DIAGNOSTICS rows_affected = ROW_COUNT manquant")

    if has_dyn:
        raise DynamicExecuteForbiddenError("EXECUTE dynamique interdit dans un atome")

    declared = {a.table for a in manifest.affects}
    undeclared = touched - declared
    if undeclared:
        raise TableNotDeclaredError(f"table non declaree dans le manifeste: {sorted(undeclared)}")

    # --- 8 (last, cheap) : COMMENT ON PROCEDURE present ---
    if _find_comment_on_procedure(raw_stmts) is None:
        raise CommentMissingError(f"COMMENT ON PROCEDURE {manifest.name} manquant")
