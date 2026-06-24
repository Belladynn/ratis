"""HSP4 — tests fonctionnels du rôle PG agent_read.

Ouvre une 2ᵉ connexion à la DB jetable en tant qu'``agent_read`` et
tente les SELECT/INSERT/UPDATE listés dans la spec §M1. Catch tout drift
entre les privileges déclarés (test_hsp4_migration.py) et l'enforcement
moteur PG réel.

Catégories couvertes :
1. Tables interdites totales (7) → SELECT doit lever ProgrammingError.
2. Colonnes REVOKE sur users/subscriptions/scans → permission denied.
3. Colonnes safe → SELECT OK.
4. Tables ouvertes → SELECT OK.
5. Pas d'INSERT/UPDATE/DELETE nulle part (CALL via procédure = Task 4).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from ._alembic_fixture import spin_up_migrated_db

# Mot de passe synchronisé avec conftest.py (os.environ.setdefault).
_AGENT_PWD = "test-agent-read-password-32-chars!"  # pragma: allowlist secret


@pytest.fixture(scope="module")
def migrated_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp4_role")


def _agent_engine(admin_url: str):
    """Construit une URL ``agent_read:<pwd>@host/dbname`` depuis l'URL admin.

    L'URL admin a la forme
    ``postgresql+psycopg://ratis:ratis@localhost:5432/<db>``.
    On remplace uniquement le segment user:password.
    """
    after_at = admin_url.split("@", 1)[1]
    return create_engine(
        f"postgresql+psycopg://agent_read:{_AGENT_PWD}@{after_at}",
        isolation_level="AUTOCOMMIT",
    )


# ---------------------------------------------------------------------------
# 1. Tables interdites totales — SELECT doit lever ProgrammingError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "db_change_log",
        "db_write_approvals",
        "app_settings",
        "admin_settings_audit",
        "refresh_tokens",
        "user_push_tokens",
        "user_identities",
    ],
)
def test_agent_cannot_select_forbidden_table(migrated_db_url: str, table: str) -> None:
    """agent_read ne peut pas SELECT depuis les tables totalement interdites."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        assert "permission denied" in str(exc_info.value).lower(), (
            f"Attendu 'permission denied' pour {table} — obtenu : {exc_info.value}"
        )


# ---------------------------------------------------------------------------
# 2. Colonnes REVOKE sur users — permission denied
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("col", ["email", "password_hash", "support_id", "ref_lat"])
def test_agent_cannot_select_pii_column_users(migrated_db_url: str, col: str) -> None:
    """agent_read ne peut pas SELECT les colonnes PII de users."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text(f"SELECT {col} FROM users LIMIT 1"))
        assert "permission denied" in str(exc_info.value).lower(), (
            f"Attendu 'permission denied' pour users.{col} — obtenu : {exc_info.value}"
        )


def test_agent_cannot_select_payment_ref_from_subscriptions(migrated_db_url: str) -> None:
    """agent_read ne peut pas SELECT subscriptions.payment_ref (donnée bancaire)."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text("SELECT payment_ref FROM subscriptions LIMIT 1"))
        assert "permission denied" in str(exc_info.value).lower()


def test_agent_cannot_select_user_lat_from_scans(migrated_db_url: str) -> None:
    """agent_read ne peut pas SELECT scans.user_lat (données géo PII)."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text("SELECT user_lat FROM scans LIMIT 1"))
        assert "permission denied" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 3. Colonnes safe — SELECT OK
# ---------------------------------------------------------------------------


def test_agent_can_select_id_from_users(migrated_db_url: str) -> None:
    """agent_read peut SELECT users.id (colonne non-PII autorisée)."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        conn.execute(text("SELECT id FROM users LIMIT 1"))


def test_agent_can_select_id_from_subscriptions(migrated_db_url: str) -> None:
    """agent_read peut SELECT subscriptions.id (colonne non-PII autorisée)."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        conn.execute(text("SELECT id FROM subscriptions LIMIT 1"))


def test_agent_can_select_id_from_scans(migrated_db_url: str) -> None:
    """agent_read peut SELECT scans.id (colonne non-PII autorisée)."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        conn.execute(text("SELECT id FROM scans LIMIT 1"))


# ---------------------------------------------------------------------------
# 4. Tables ouvertes — SELECT OK
# ---------------------------------------------------------------------------


def test_agent_can_select_cabecoin_transactions(migrated_db_url: str) -> None:
    """agent_read peut SELECT cabecoin_transactions (table ouverte intégralement)."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        conn.execute(text("SELECT id FROM cabecoin_transactions LIMIT 1"))


# ---------------------------------------------------------------------------
# 5. Pas d'INSERT/UPDATE/DELETE nulle part
# ---------------------------------------------------------------------------


def test_agent_cannot_insert_into_users(migrated_db_url: str) -> None:
    """agent_read ne peut pas INSERT INTO users."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(
                text("INSERT INTO users (id, email, account_type) VALUES (:id, :e, 'internal')"),
                {"id": str(uuid.uuid4()), "e": "x@example.com"},
            )
        assert "permission denied" in str(exc_info.value).lower()


def test_agent_cannot_update_user_cab_balance(migrated_db_url: str) -> None:
    """agent_read ne peut pas UPDATE user_cab_balance."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text("UPDATE user_cab_balance SET balance = 0 WHERE user_id = gen_random_uuid()"))
        assert "permission denied" in str(exc_info.value).lower()


def test_agent_cannot_delete_from_scans(migrated_db_url: str) -> None:
    """agent_read ne peut pas DELETE FROM scans."""
    eng = _agent_engine(migrated_db_url)
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError) as exc_info:
            conn.execute(text("DELETE FROM scans WHERE id = gen_random_uuid()"))
        assert "permission denied" in str(exc_info.value).lower()
