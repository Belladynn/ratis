"""HSP3 — migration tests : colonne ``mode`` + seeds ``app_settings``.

Utilise la fixture HSP2 ``spin_up_migrated_db`` pour observer en base réelle
le résultat de ``alembic upgrade head``. Tests indépendants du runtime PA —
on lit Postgres directement via SQLAlchemy.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def hsp3_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp3_mig")


def test_db_write_approvals_has_mode_column(hsp3_db_url: str) -> None:
    """The ``mode`` column exists after the HSP3 migration."""
    eng = create_engine(hsp3_db_url)
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='db_write_approvals' AND column_name='mode'"
                )
            ).first()
            assert row is not None, "mode column missing"
            assert row[0] == "text"
    finally:
        eng.dispose()


def test_db_write_approvals_mode_defaults_to_execute(hsp3_db_url: str) -> None:
    """A row inserted without explicit ``mode`` gets ``'execute'`` server-side."""
    import uuid

    eng = create_engine(hsp3_db_url)
    try:
        sid = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO db_write_approvals "
                    "(submission_id, payload, resume_url) "
                    "VALUES (:sid, '{}'::jsonb, 'http://example.invalid/r')"
                ),
                {"sid": sid},
            )
        with eng.connect() as conn:
            mode = conn.execute(
                text("SELECT mode FROM db_write_approvals WHERE submission_id=:sid"),
                {"sid": sid},
            ).scalar_one()
            assert mode == "execute"
    finally:
        eng.dispose()


def test_db_write_approvals_mode_rejects_invalid_value(hsp3_db_url: str) -> None:
    """The CHECK constraint rejects ``mode`` values outside the whitelist."""
    import uuid

    from sqlalchemy.exc import IntegrityError

    eng = create_engine(hsp3_db_url)
    try:
        sid = uuid.uuid4()
        with eng.begin() as conn, pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO db_write_approvals "
                    "(submission_id, mode, payload, resume_url) "
                    "VALUES (:sid, 'autoexec', '{}'::jsonb, 'http://x.invalid')"
                ),
                {"sid": sid},
            )
    finally:
        eng.dispose()


def test_app_settings_human_approval_seed_present(hsp3_db_url: str) -> None:
    """The HSP3 migration seeds ``app_settings`` section ``human_approval``.

    At seed time the secret_set flag is false — the secret is installed
    by a separate ops act (script ``init-human-approval-secret.py``).
    """
    eng = create_engine(hsp3_db_url)
    try:
        with eng.connect() as conn:
            data = conn.execute(text("SELECT data FROM app_settings WHERE section='human_approval'")).scalar_one()
            assert data == {"secret_set": False, "argon2_hash": None}
    finally:
        eng.dispose()


def test_app_settings_db_pipeline_trust_levels_seed_present(hsp3_db_url: str) -> None:
    """``db_pipeline_trust_levels`` lists every HSP1 atom, all at ``manual``.

    HSP3 (``apply_hsp3_human_gate``) seeds the 3 original atoms ; the later
    ``apply_reset_stuck_route`` migration appends a 4th atom
    (``support_reset_stuck_optimized_route``) via a JSONB concat, also at
    ``manual``. The assertion reflects the full state after
    ``alembic upgrade heads`` — the source of truth for this real-DB test.
    """
    eng = create_engine(hsp3_db_url)
    try:
        with eng.connect() as conn:
            data = conn.execute(
                text("SELECT data FROM app_settings WHERE section='db_pipeline_trust_levels'")
            ).scalar_one()
            assert data == {
                "support_credit_cab": "manual",
                "support_debit_cab": "manual",
                "support_link_scan_to_user": "manual",
                "support_reset_stuck_optimized_route": "manual",
            }
    finally:
        eng.dispose()


def test_app_settings_n8n_resume_secret_sentinel_present(hsp3_db_url: str) -> None:
    """A sentinel row tracks whether ``N8N_RESUME_SECRET`` env was set at
    last lifespan boot — the value itself lives in env only."""
    eng = create_engine(hsp3_db_url)
    try:
        with eng.connect() as conn:
            data = conn.execute(text("SELECT data FROM app_settings WHERE section='n8n_resume_secret'")).scalar_one()
            assert data == {"set": False}
    finally:
        eng.dispose()
