"""HSP4 — migration apply_hsp4_agent_confinement : tests via spin_up_migrated_db.

Vérifie via une DB jetable (fixture HSP2) que la migration crée le rôle
``agent_read``, applique les REVOKE/GRANT attendus, et que ``statement_timeout``
est posé.

NOTE Task 1 : le GRANT EXECUTE sur les procédures HSP1 est implémenté en
Task 4 (séparée). Ce fichier ne le teste pas. Task 1 valide uniquement la
PRESENCE des GRANT/REVOKE de tables/colonnes via ``has_*_privilege``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def migrated_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp4_mig")


def test_role_agent_read_exists_after_upgrade(migrated_db_url: str) -> None:
    """Après upgrade, le rôle ``agent_read`` existe dans pg_roles."""
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT rolname, rolcanlogin, rolinherit FROM pg_roles WHERE rolname = 'agent_read'")
        ).first()
    assert row is not None, "rôle agent_read absent — migration HSP4 pas appliquée"
    assert row.rolcanlogin is True, "agent_read doit avoir LOGIN"
    assert row.rolinherit is False, "agent_read doit être NOINHERIT (cf spec §M1)"


def test_role_agent_read_has_5s_statement_timeout(migrated_db_url: str) -> None:
    """``ALTER ROLE agent_read SET statement_timeout='5s'`` posé par la migration."""
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT setconfig FROM pg_db_role_setting "
                "WHERE setrole = (SELECT oid FROM pg_roles WHERE rolname = 'agent_read')"
            )
        ).first()
    assert row is not None, "pg_db_role_setting vide pour agent_read"
    assert any(s.startswith("statement_timeout=5s") for s in (row.setconfig or [])), (
        f"statement_timeout=5s absent — setconfig={row.setconfig!r}"
    )


def test_forbidden_tables_revoked(migrated_db_url: str) -> None:
    """Les 7 tables interdites n'ont aucun privilege accordé à agent_read."""
    forbidden = [
        "db_change_log",
        "db_write_approvals",
        "app_settings",
        "admin_settings_audit",
        "refresh_tokens",
        "user_push_tokens",
        "user_identities",
    ]
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for tbl in forbidden:
            row = conn.execute(
                text("SELECT has_table_privilege('agent_read', :t, 'SELECT') AS can_sel"),
                {"t": f"public.{tbl}"},
            ).first()
            assert row.can_sel is False, f"agent_read peut SELECT {tbl} — REVOKE manquant"


def test_users_pii_columns_revoked(migrated_db_url: str) -> None:
    """``users`` : colonnes PII REVOKE, colonnes safe GRANT.

    Note : ``provider_id`` n'existe plus depuis la migration OAuth-only
    ``20260518_1300_acct_type`` — les OAuth ids vivent dans ``user_identities``
    (interdite intégralement, cf test ci-dessus).
    """
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for col in (
            "email",
            "password_hash",
            "support_id",
            "ref_lat",
            "ref_lng",
            "password_changed_at",
        ):
            row = conn.execute(
                text("SELECT has_column_privilege('agent_read', 'public.users', :c, 'SELECT') AS can_sel"),
                {"c": col},
            ).first()
            assert row.can_sel is False, f"agent_read peut SELECT users.{col} — REVOKE colonne manquant"
        for col in ("id", "display_name", "trust_score", "created_at"):
            row = conn.execute(
                text("SELECT has_column_privilege('agent_read', 'public.users', :c, 'SELECT') AS can_sel"),
                {"c": col},
            ).first()
            assert row.can_sel is True, f"agent_read ne peut PAS SELECT users.{col} — GRANT manquant"


def test_subscriptions_pii_columns_revoked(migrated_db_url: str) -> None:
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for col in (
            "payment_ref",
            "stripe_session_id",
            "discount_campaign_code",
            "discount_amount",
        ):
            row = conn.execute(
                text("SELECT has_column_privilege('agent_read', 'public.subscriptions', :c, 'SELECT') AS can_sel"),
                {"c": col},
            ).first()
            assert row.can_sel is False, f"agent_read peut SELECT subscriptions.{col} — REVOKE manquant"
        for col in ("id", "user_id", "status", "plan", "started_at", "expires_at"):
            row = conn.execute(
                text("SELECT has_column_privilege('agent_read', 'public.subscriptions', :c, 'SELECT') AS can_sel"),
                {"c": col},
            ).first()
            assert row.can_sel is True, f"agent_read ne peut PAS SELECT subscriptions.{col}"


def test_scans_pii_geo_columns_revoked(migrated_db_url: str) -> None:
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for col in ("user_lat", "user_lng"):
            row = conn.execute(
                text("SELECT has_column_privilege('agent_read', 'public.scans', :c, 'SELECT') AS can_sel"),
                {"c": col},
            ).first()
            assert row.can_sel is False, f"agent_read peut SELECT scans.{col} — REVOKE PII géo manquant"
        for col in ("id", "user_id", "product_ean", "store_id", "status"):
            row = conn.execute(
                text("SELECT has_column_privilege('agent_read', 'public.scans', :c, 'SELECT') AS can_sel"),
                {"c": col},
            ).first()
            assert row.can_sel is True, f"agent_read ne peut PAS SELECT scans.{col}"


def test_open_table_cabecoin_transactions_grant(migrated_db_url: str) -> None:
    """``cabecoin_transactions`` est lisible intégralement par agent_read."""
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT has_table_privilege('agent_read', 'public.cabecoin_transactions', 'SELECT') AS can_sel")
        ).first()
    assert row.can_sel is True, "agent_read ne peut pas SELECT cabecoin_transactions"


def test_no_insert_update_delete_on_any_table(migrated_db_url: str) -> None:
    """``agent_read`` n'a aucun privilege d'écriture sur AUCUNE table (sauf via CALL)."""
    eng = create_engine(migrated_db_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        # Échantillon représentatif — tables ouvertes en SELECT mais jamais en WRITE.
        for tbl in ("cabecoin_transactions", "user_cab_balance", "scans", "stores", "products"):
            for priv in ("INSERT", "UPDATE", "DELETE"):
                row = conn.execute(
                    text("SELECT has_table_privilege('agent_read', :t, :p) AS can"),
                    {"t": f"public.{tbl}", "p": priv},
                ).first()
                assert row.can is False, f"agent_read peut {priv} {tbl} — privilege WRITE doit être interdit"
