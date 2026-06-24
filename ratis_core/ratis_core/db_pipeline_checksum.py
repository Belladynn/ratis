"""HSP4 M5 — checksum per-call.

Confronte l'effet **réel** d'un CALL (mesuré via db_change_log HSP2) à
l'effet **déclaré** (ProcedureManifest.affects HSP1).

Cas d'usage : la pipeline n8n, après un CALL, appelle cet helper pour
décider COMMIT ou ROLLBACK. Single source de vérité (Python). n8n passe
par l'endpoint `/api/v1/admin/db-pipeline/check-rowcount` qui délègue
à cette fonction.

Comportement :
* aggregate `db_change_log` par `(table_name)` filtré sur `submission_id` ;
* compare au dict `{affects[i].table: affects[i].rows}` exactement ;
* renvoie `{ok, observed, expected, mismatches}`.

`mismatches` est une liste de strings lisibles — n8n l'inclut dans son
alerte Discord en cas de freeze.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from .db_procedure_manifest import ProcedureManifest


def check_rowcount(
    db: Session,
    submission_id: UUID,
    manifest: ProcedureManifest,
) -> dict:
    """Compare l'effet observé (db_change_log) à l'effet déclaré (manifest.affects).

    Args :
        db : session active (la même que celle qui a CALL la procédure —
             la query SELECT doit voir les rows insérées par le trigger
             AFTER dans la même transaction).
        submission_id : UUID posé par `SET LOCAL app.submission_id` avant le CALL.
        manifest : manifeste HSP1 de la procédure appelée.

    Retourne un dict :
        {
            "ok": bool,
            "observed": {table: rowcount} — depuis db_change_log,
            "expected": {table: rows} — depuis manifest.affects,
            "mismatches": [str] — descriptions lisibles des divergences.
        }
    """
    rows = db.execute(
        text(
            "SELECT table_name, COUNT(*)::int AS n FROM db_change_log "
            "WHERE submission_id = CAST(:s AS uuid) GROUP BY table_name"
        ),
        {"s": str(submission_id)},
    ).fetchall()
    observed: dict[str, int] = {r.table_name: r.n for r in rows}
    expected: dict[str, int] = {a.table: a.rows for a in manifest.affects}

    mismatches: list[str] = []

    # Tables observed mais non déclarées (= effet de bord caché).
    for table in observed:
        if table not in expected:
            mismatches.append(
                f"table `{table}` touchée mais absente de manifest.affects (observed={observed[table]} rows)"
            )

    # Tables déclarées mais absentes (= procédure n'a pas écrit ce qu'elle
    # avait promis — ou args ne matchent aucune row).
    for table, exp in expected.items():
        obs = observed.get(table, 0)
        if obs != exp:
            mismatches.append(f"table `{table}` : observed={obs} rows, expected={exp}")

    return {
        "ok": not mismatches,
        "observed": observed,
        "expected": expected,
        "mismatches": mismatches,
    }
