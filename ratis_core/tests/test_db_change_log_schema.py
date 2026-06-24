"""Tests for the ``DbChangeLog`` SQLAlchemy model.

HSP2 — the model is mostly a read-only handle on the PG-managed table
populated by ``fn_db_change_log_record()`` triggers. We don't INSERT into
``db_change_log`` from Python ; the trigger does. These tests therefore
validate **shape** (columns, types, default behaviours) and the
trigger-driven semantics (append-only) via the alembic-spinup fixture
introduced in Tasks 2/3.

This file currently holds only the smoke import test — DB-level tests
land in Task 2 (schema + append-only guards) and Task 3 (record trigger).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from ratis_core.models import DbChangeLog
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import InternalError, ProgrammingError


def test_db_change_log_class_exists_and_exposes_expected_columns() -> None:
    """Smoke : the class is importable from ratis_core.models and has the
    columns the spec describes (id, submission_id, table_name, op, old_data,
    new_data, created_at)."""
    cols = {c.name for c in inspect(DbChangeLog).columns}
    assert cols == {
        "id",
        "submission_id",
        "table_name",
        "op",
        "old_data",
        "new_data",
        "created_at",
    }


def test_db_change_log_primary_key_is_uuid_with_server_default() -> None:
    """``id`` is uuid, primary key, with ``gen_random_uuid()`` server default."""
    pk_cols = [c for c in inspect(DbChangeLog).columns if c.primary_key]
    assert len(pk_cols) == 1
    pk = pk_cols[0]
    assert pk.name == "id"
    assert pk.type.python_type is uuid.UUID
    assert pk.server_default is not None
    assert "gen_random_uuid" in str(pk.server_default.arg)


def test_db_change_log_created_at_is_timestamptz_with_server_default_now() -> None:
    """``created_at`` is timestamptz, NOT NULL, defaults to ``now()``."""
    col = inspect(DbChangeLog).columns["created_at"]
    assert col.nullable is False
    assert col.type.python_type is datetime
    assert getattr(col.type, "timezone", False) is True
    assert col.server_default is not None
    assert "now" in str(col.server_default.arg).lower()


def test_db_change_log_submission_id_is_nullable_uuid() -> None:
    """``submission_id`` is uuid, **nullable** — operations outside a pipeline
    transaction (bootstrap, batches) record with NULL ``submission_id``."""
    col = inspect(DbChangeLog).columns["submission_id"]
    assert col.nullable is True
    assert col.type.python_type is uuid.UUID


def test_db_change_log_op_has_check_constraint_on_insert_update_delete() -> None:
    """The PG-level CHECK constraint is mirrored on the model so
    ``Base.metadata.create_all()`` test setups raise IntegrityError on the
    same invalid values prod hits (op ∈ {insert, update, delete})."""
    table = inspect(DbChangeLog).local_table
    check_sqls = [str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"]
    joined = " ".join(check_sqls).lower()
    assert "insert" in joined
    assert "update" in joined
    assert "delete" in joined


# ---------------------------------------------------------------------------
# DB-level tests — require the HSP2 migration applied. We spin up a fresh DB
# via the helper in ``_alembic_fixture.py`` to observe real PG triggers.
# ---------------------------------------------------------------------------

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def hsp2_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp2_schema")


def test_db_change_log_table_exists_on_migrated_db(hsp2_db_url: str) -> None:
    """After ``alembic upgrade head``, ``db_change_log`` exists in public."""
    eng = create_engine(hsp2_db_url)
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relname = 'db_change_log'"
                )
            ).first()
            assert row is not None, "db_change_log table missing after migration"
    finally:
        eng.dispose()


def test_db_change_log_indexes_present(hsp2_db_url: str) -> None:
    """Both ``idx_db_change_log_submission`` and
    ``idx_db_change_log_table_time`` are created by the migration."""
    eng = create_engine(hsp2_db_url)
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'db_change_log'")
            ).all()
            names = {r[0] for r in rows}
            assert "idx_db_change_log_submission" in names
            assert "idx_db_change_log_table_time" in names
    finally:
        eng.dispose()


def test_db_change_log_update_raises_append_only(hsp2_db_url: str) -> None:
    """``UPDATE db_change_log`` raises (trigger ``trg_db_change_log_no_update``)."""
    eng = create_engine(hsp2_db_url)
    try:
        with eng.begin() as conn:
            # Seed one row via direct INSERT — this is allowed at the SQL
            # level (the guard triggers cover UPDATE/DELETE only).
            conn.execute(
                text("INSERT INTO db_change_log (table_name, op, new_data) VALUES ('test', 'insert', '{}'::jsonb)")
            )
        with eng.begin() as conn:
            with pytest.raises((InternalError, ProgrammingError)) as exc_info:
                conn.execute(text("UPDATE db_change_log SET table_name = 'x'"))
            assert "append-only" in str(exc_info.value).lower()
    finally:
        eng.dispose()


def test_db_change_log_delete_raises_append_only(hsp2_db_url: str) -> None:
    """``DELETE FROM db_change_log`` raises (trigger ``trg_db_change_log_no_delete``)."""
    eng = create_engine(hsp2_db_url)
    try:
        with eng.begin() as conn:
            conn.execute(
                text("INSERT INTO db_change_log (table_name, op, new_data) VALUES ('test_del', 'insert', '{}'::jsonb)")
            )
        with eng.begin() as conn:
            with pytest.raises((InternalError, ProgrammingError)) as exc_info:
                conn.execute(text("DELETE FROM db_change_log WHERE table_name = 'test_del'"))
            assert "append-only" in str(exc_info.value).lower()
    finally:
        eng.dispose()
