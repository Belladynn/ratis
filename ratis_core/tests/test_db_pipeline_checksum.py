"""HSP4 M5 — checksum per-call : confronte db_change_log au manifeste.

Tests via la fixture HSP2 (DB jetable migrée) — on insère des lignes dans
`db_change_log` à la main avec un `submission_id` connu, puis on vérifie
que `check_rowcount` compare correctement au manifeste.
"""

from __future__ import annotations

import uuid

import pytest
from ratis_core.db_pipeline_checksum import check_rowcount
from ratis_core.db_procedure_manifest import ProcedureManifest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def migrated_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp4_check")


def _credit_cab_manifest() -> ProcedureManifest:
    """1 UPDATE user_cab_balance + 1 INSERT cabecoin_transactions."""
    return ProcedureManifest.model_validate(
        {
            "name": "support_credit_cab",
            "purpose": "test",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [
                {"name": "p_user_id", "type": "uuid", "required": True},
                {"name": "p_amount", "type": "integer", "required": True},
            ],
            "affects": [
                {"table": "user_cab_balance", "op": "update", "rows": 1},
                {"table": "cabecoin_transactions", "op": "insert", "rows": 1},
            ],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        }
    )


def _insert_change_log(db: Session, submission_id: uuid.UUID, table: str, op: str, n: int = 1) -> None:
    for _ in range(n):
        db.execute(
            text(
                "INSERT INTO db_change_log (submission_id, table_name, op, new_data) "
                "VALUES (CAST(:s AS uuid), :t, :o, '{}'::jsonb)"
            ),
            {"s": str(submission_id), "t": table, "o": op},
        )


def test_check_rowcount_matches_manifest(migrated_db_url: str) -> None:
    eng = create_engine(migrated_db_url)
    with Session(eng) as db:
        sid = uuid.uuid4()
        _insert_change_log(db, sid, "user_cab_balance", "update", n=1)
        _insert_change_log(db, sid, "cabecoin_transactions", "insert", n=1)
        db.commit()

        result = check_rowcount(db, sid, _credit_cab_manifest())
    assert result["ok"] is True
    assert result["mismatches"] == []
    assert result["observed"] == {"user_cab_balance": 1, "cabecoin_transactions": 1}
    assert result["expected"] == {"user_cab_balance": 1, "cabecoin_transactions": 1}


def test_check_rowcount_rejects_too_many_rows(migrated_db_url: str) -> None:
    """Procédure boguée qui UPDATE 2 rows (manifest dit 1) → ok=False."""
    eng = create_engine(migrated_db_url)
    with Session(eng) as db:
        sid = uuid.uuid4()
        _insert_change_log(db, sid, "user_cab_balance", "update", n=2)
        _insert_change_log(db, sid, "cabecoin_transactions", "insert", n=1)
        db.commit()

        result = check_rowcount(db, sid, _credit_cab_manifest())
    assert result["ok"] is False
    assert any("user_cab_balance" in m for m in result["mismatches"])


def test_check_rowcount_rejects_undeclared_table(migrated_db_url: str) -> None:
    """Procédure qui INSERT dans une table non déclarée → ok=False."""
    eng = create_engine(migrated_db_url)
    with Session(eng) as db:
        sid = uuid.uuid4()
        _insert_change_log(db, sid, "user_cab_balance", "update", n=1)
        _insert_change_log(db, sid, "cabecoin_transactions", "insert", n=1)
        _insert_change_log(db, sid, "scans", "update", n=1)  # non déclarée
        db.commit()

        result = check_rowcount(db, sid, _credit_cab_manifest())
    assert result["ok"] is False
    assert any("scans" in m for m in result["mismatches"])


def test_check_rowcount_rejects_missing_declared_table(migrated_db_url: str) -> None:
    """Manifest déclare 2 tables mais une seule a été touchée → ok=False."""
    eng = create_engine(migrated_db_url)
    with Session(eng) as db:
        sid = uuid.uuid4()
        _insert_change_log(db, sid, "user_cab_balance", "update", n=1)
        # cabecoin_transactions absent
        db.commit()

        result = check_rowcount(db, sid, _credit_cab_manifest())
    assert result["ok"] is False
    assert any("cabecoin_transactions" in m for m in result["mismatches"])


def test_check_rowcount_empty_observed_with_empty_manifest(migrated_db_url: str) -> None:
    """Manifest sans `affects` et aucune ligne loggée → ok=True (cas read-only théorique)."""
    eng = create_engine(migrated_db_url)
    with Session(eng) as db:
        sid = uuid.uuid4()
        # Aucun INSERT dans db_change_log.
        db.commit()

        empty_manifest = ProcedureManifest.model_validate(
            {
                "name": "test_readonly",
                "purpose": "noop",
                "facing": True,
                "direction": "fix",
                "money_tier": "non_money",
                "args": [],
                "affects": [],
                "trust_level_initial": "manual",
                "allowed_callers": ["claude-code-main"],
            }
        )
        result = check_rowcount(db, sid, empty_manifest)
    assert result["ok"] is True
    assert result["observed"] == {}
    assert result["expected"] == {}
    assert result["mismatches"] == []


def test_check_rowcount_isolated_per_submission(migrated_db_url: str) -> None:
    """Deux submissions distinctes ne se contaminent pas mutuellement."""
    eng = create_engine(migrated_db_url)
    with Session(eng) as db:
        sid_a, sid_b = uuid.uuid4(), uuid.uuid4()
        _insert_change_log(db, sid_a, "user_cab_balance", "update", n=1)
        _insert_change_log(db, sid_a, "cabecoin_transactions", "insert", n=1)
        _insert_change_log(db, sid_b, "scans", "update", n=5)
        db.commit()

        result_a = check_rowcount(db, sid_a, _credit_cab_manifest())
    assert result_a["ok"] is True
    assert "scans" not in result_a["observed"]
