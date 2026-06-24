"""Pattern A guard — keep ORM ``__table_args__`` CHECK constraints in sync with PG schema.

Audit finding (2026-05-11): ORM models declared 60/172 PG CHECK constraints
(~35% coverage). Tests using ``Base.metadata.create_all()`` therefore ran
against a permissive schema and gave false confidence that invalid rows
would be rejected. Worse offenders: ``stores`` 2/9, ``products`` 1/8,
``cashback_withdrawals`` 0/7, ``discount_campaigns`` 0/10.

This test builds an isolated PG database by running ``alembic upgrade head``
(production source of truth) and then, for every ORM-mapped table, asserts
that the set of CHECK constraint **names** declared in ``__table_args__``
exactly matches the set materialised in the migration-built schema.

If the test fails after a schema change, the procedure is :

1. Identify the missing constraint name(s) from the assertion message.
2. Add them to the ORM model's ``__table_args__`` with the same ``name=``
   used in PG so future renames stay traceable. Mirror the SQL predicate
   from the PG ``\\d <table>`` output or from ``pg_get_constraintdef``.
3. Re-run this test.

A few tables intentionally hold ORM-only constraints (e.g. ``subscriptions``
has ``subscriptions_plan_check`` declared in the ORM but the production
schema is missing the matching CHECK due to an oversight in migration
``d0e1f2a3b4c5`` — tracked in ``DECISIONS_PENDING.md``). These are listed
in ``ORM_ONLY_CONSTRAINTS`` below and ignored by the diff so the test stays
green until the follow-up migration lands.

The test is **skipped automatically** when the Alembic harness cannot be
reached (e.g. Postgres not running locally). CI Linux Docker has both, so
this test executes there and gates the merge per R15.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from ratis_core.database import Base
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CheckConstraint

# Tables (or whole table-args lists) where the ORM legitimately declares a
# CHECK that PG does not yet enforce. Each entry is a tuple (table, conname)
# with a reason. Keep this list short and tracked in DECISIONS_PENDING.md.
ORM_ONLY_CONSTRAINTS: set[tuple[str, str]] = {
    # Migration d0e1f2a3b4c5 added subscriptions.plan but the docstring's
    # promised "CHECK (plan IN ('monthly', 'annual'))" was never executed.
    # ORM keeps the intent; a follow-up migration must add it to PG.
    ("subscriptions", "subscriptions_plan_check"),
}

# Tables / constraints temporarily NOT mirrored in the ORM yet because the
# existing test fixtures across services insert rows that would violate them.
# Each follow-up PR will : (a) update the fixtures, (b) add the
# CheckConstraint to the model's __table_args__, (c) remove the entry from
# this set. Tracked in DECISIONS_PENDING.md (Pattern A roll-out plan).
DEFERRED_PG_ONLY_CONSTRAINTS: set[tuple[str, str]] = set()

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_URL = "postgresql+psycopg://ratis:ratis@localhost:5432/postgres"  # pragma: allowlist secret


def _orm_check_names(table_name: str) -> set[str]:
    """Names of CheckConstraint objects declared on the ORM table."""
    table = Base.metadata.tables[table_name]
    return {c.name for c in table.constraints if isinstance(c, CheckConstraint) and c.name}


def _pg_check_names(conn, table_name: str) -> set[str]:
    """Names of CHECK constraints currently present in the connected PG schema."""
    rows = conn.execute(
        text(
            """
            SELECT con.conname
              FROM pg_constraint con
              JOIN pg_class c ON c.oid = con.conrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE con.contype = 'c'
               AND n.nspname = 'public'
               AND c.relname = :tname
            """
        ),
        {"tname": table_name},
    ).all()
    return {r[0] for r in rows}


@pytest.fixture(scope="module")
def migrated_db_url() -> str:
    """Spin up a disposable DB, run ``alembic upgrade head``, return its URL.

    Falls back to ``pytest.skip`` if Postgres or the alembic CLI is unreachable.
    """
    db_name = f"ratis_schema_sync_{uuid.uuid4().hex[:8]}"
    fresh_url = f"postgresql+psycopg://ratis:ratis@localhost:5432/{db_name}"  # pragma: allowlist secret

    try:
        admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            conn.execute(text(f"CREATE DATABASE {db_name}"))
        admin_engine.dispose()
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable for schema-sync test: {exc}")

    env = os.environ.copy()
    env["DATABASE_URL"] = fresh_url
    try:
        result = subprocess.run(
            # Use ``heads`` (plural) so the test keeps working while the
            # repo has multiple migration branches mid-flight — alembic
            # collapses them into a single applied state.
            ["uv", "run", "alembic", "upgrade", "heads"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        # Cleanup before skipping so we don't leak the DB
        _drop_db(db_name)
        pytest.skip(f"alembic CLI unavailable for schema-sync test: {exc}")

    if result.returncode != 0:
        _drop_db(db_name)
        pytest.fail(
            f"alembic upgrade head failed during schema-sync test:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    yield fresh_url
    _drop_db(db_name)


def _drop_db(db_name: str) -> None:
    """Best-effort cleanup of a disposable test database."""
    try:
        admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            # Force-terminate any lingering session before DROP
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": db_name},
            )
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        admin_engine.dispose()
    except OperationalError:
        pass


def test_orm_check_constraints_match_pg(migrated_db_url: str):
    """Every PG CHECK on an ORM-mapped table must be declared on the model.

    Direction is **PG → ORM**: the schema produced by ``alembic upgrade head``
    is the source of truth. The ORM ``__table_args__`` must mirror those
    CHECKs so that test runs using ``Base.metadata.create_all()`` reject the
    same invalid rows production would.
    """
    drift: dict[str, dict[str, set[str]]] = {}
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            for table_name in Base.metadata.tables:
                pg_names = _pg_check_names(conn, table_name)
                orm_names = _orm_check_names(table_name)
                deferred_here = {name for (t, name) in DEFERRED_PG_ONLY_CONSTRAINTS if t == table_name}
                missing_in_orm = pg_names - orm_names - deferred_here
                extra_in_orm = orm_names - pg_names - {name for (t, name) in ORM_ONLY_CONSTRAINTS if t == table_name}
                if missing_in_orm or extra_in_orm:
                    drift[table_name] = {
                        "missing_in_orm": missing_in_orm,
                        "extra_in_orm": extra_in_orm,
                    }
    finally:
        engine.dispose()

    if drift:
        lines = ["ORM ↔ PG CHECK constraint drift detected:"]
        for table_name, d in sorted(drift.items()):
            if d["missing_in_orm"]:
                lines.append(f"  [{table_name}] missing in ORM __table_args__: {sorted(d['missing_in_orm'])}")
            if d["extra_in_orm"]:
                lines.append(f"  [{table_name}] in ORM but not in PG (rename ? dead ?): {sorted(d['extra_in_orm'])}")
        raise AssertionError("\n".join(lines))


# Mirror of ``_EXCLUDED_TABLES`` in ``alembic/env.py`` — tables not managed by
# SQLAlchemy models (datafix infra) plus ``spatial_ref_sys`` (system table
# created by the PostGIS extension). Keep in sync with ``env.py``.
_AUTOGEN_EXCLUDED_TABLES = {"datafix_logs", "datafix_backup", "spatial_ref_sys"}


def _autogen_include_object(obj, name, type_, reflected, compare_to):
    """Mirror of ``include_object`` in ``alembic/env.py``.

    Must stay identical to the production autogenerate filter so this test
    reproduces exactly what ``alembic check`` would report. ``env.py`` runs
    migrations at import time, so its filter cannot be imported directly —
    keep this in sync if ``env.py`` changes.
    """
    if type_ == "table" and name in _AUTOGEN_EXCLUDED_TABLES:
        return False
    return type_ != "index"


def test_no_autogenerate_drift(migrated_db_url: str):
    """``alembic check`` equivalent — ORM metadata must match the migrated DB.

    Runs Alembic's autogenerate diff (``compare_metadata``) of
    ``Base.metadata`` against a database built by ``alembic upgrade head``.
    A non-empty diff means a model declares a column type / nullability /
    constraint that the migrations do not produce (or vice versa) — the
    exact drift class ``alembic check`` gates on in CI.

    Locks in the 2026-05-14 fix for the 6 model/DB drifts :
      - 3 ``modify_type`` : naive ``DateTime`` shorthand on
        ``app_settings.updated_at`` and ``notification_outbox.{created_at,
        sent_at}`` vs PG ``TIMESTAMPTZ`` (cf KP-44).
      - 2 ``modify_nullable`` : ``products`` / ``stores`` ``name_normalized``
        generated columns made ``NOT NULL`` to match the ORM intent.
      - 1 ``add_constraint`` : ``users.support_id`` modelled as a unique
        ``Index`` instead of ``unique=True`` (PG has a unique index).
    """
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "compare_server_default": False,
                    "include_object": _autogen_include_object,
                },
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()

    assert not diff, (
        "Autogenerate drift between ORM models and the migrated schema "
        "(equivalent to a failing `alembic check`):\n" + "\n".join(f"  {op}" for op in diff)
    )
