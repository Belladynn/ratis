"""HSP4 — confinement de l'agent : rôle PG agent_read + REVOKE/GRANT.

Revision ID: 20260521_1100_hsp4_confine
Revises: 20260521_1000_hsp3_human_gate
Create Date: 2026-05-21 11:00:00.000000

Voir :
    ARCH_n8n_pipelines.md § HSP-4 (confinement agent M1-M7 — décisions consolidées post-merge ; spec d'origine récupérable via git history)

Cette migration installe :

1. Le rôle ``agent_read`` (NOINHERIT LOGIN, password lu depuis env
   ``AGENT_READ_PASSWORD``). Idempotent via DO $$ ... IF NOT EXISTS $$.
2. ``ALTER ROLE agent_read SET statement_timeout = '5s'`` (M6).
3. ``GRANT CONNECT ON DATABASE <current> TO agent_read`` + ``GRANT USAGE
   ON SCHEMA public``.
4. REVOKE total sur 7 tables interdites (cf spec §M1 annexe + ajout
   ``user_identities`` qui contient les OAuth ids PII depuis la
   migration OAuth-only ``20260518_1200_user_identities``).
5. REVOKE colonne-par-colonne sur ``users``, ``subscriptions``,
   ``scans`` (cf spec §M1 annexe — colonnes PII).
6. GRANT SELECT plein sur le reste de ``public.*`` (hors 7 interdites
   et 3 column-scoped).

NOTE : ``provider_id`` listé dans la spec §M1 a été **DROPPÉ** de
``users`` par la migration ``20260518_1300_acct_type`` (OAuth-only).
Les OAuth ids vivent maintenant dans ``user_identities`` qui est
listée dans les tables interdites (point 4).

NOTE Task 1 : le ``GRANT EXECUTE`` sur les procédures HSP1 est livré
par Task 4 (séparée). Cette migration livre uniquement la base
``agent_read`` + tables/colonnes ; l'exécution des procédures est
ajoutée incrémentalement.

Le swap ``DATABASE_URL`` → ``AGENT_DATABASE_URL`` côté agent-mcp est
HSP5. HSP4 livre uniquement la **capacité** (rôle + permissions).
"""

from __future__ import annotations

import os

from alembic import op
from sqlalchemy import text

# revision identifiers (≤32 chars per R08 — 26 chars).
revision = "20260521_1100_hsp4_confine"
down_revision = "20260521_1000_hsp3_human_gate"
branch_labels = None
depends_on = None


# Tables totalement interdites à agent_read (REVOKE all, no grant).
FORBIDDEN_TABLES = (
    "db_change_log",  # HSP2 — historique d'écriture, canal d'exfil
    "db_write_approvals",  # SP6 — resume_url, payloads, opérateur
    "app_settings",  # HSP2/HSP3 — seuils, kill-switches, trust_levels
    "admin_settings_audit",  # audit changes admin
    "refresh_tokens",  # tokens d'auth en clair (jti)
    "user_push_tokens",  # push tokens Expo
    "user_identities",  # OAuth ids PII (provider, provider_id, email)
)

# Tables où on REVOKE certaines colonnes et GRANT le reste.
COLUMN_SCOPED: dict[str, tuple[str, ...]] = {
    "users": (
        "email",
        "password_hash",
        "support_id",
        "ref_lat",
        "ref_lng",
        "password_changed_at",
        # NOTE : `provider_id` listé dans la spec a été DROPPÉ par la
        # migration OAuth-only (cf docstring). On ne le revoke pas
        # (colonne inexistante), mais user_identities est interdite
        # intégralement (FORBIDDEN_TABLES).
    ),
    "subscriptions": (
        "payment_ref",
        "stripe_session_id",
        "discount_campaign_code",
        "discount_amount",
    ),
    "scans": (
        "user_lat",
        "user_lng",
    ),
}


def upgrade() -> None:
    # 1. CREATE ROLE agent_read (idempotent), password lu depuis env.
    password = os.environ.get("AGENT_READ_PASSWORD")
    if not password:
        raise RuntimeError(
            "AGENT_READ_PASSWORD env var manquante — requise pour créer le rôle "
            "agent_read. En dev : ajoute-la dans .env.local. En prod : injectée "
            "par le runner migrations."
        )
    # Échappement basique du mot de passe pour SQL string literal.
    # SQL injection impossible car on contrôle la source (env var locale) ;
    # CREATE ROLE PASSWORD ne supporte pas la paramétrisation à l'intérieur
    # d'un bloc DO/EXECUTE, l'interpolation est inévitable. S608 est un
    # faux positif documenté ici (cf 20260510_1030_seed_achievements_v1.py).
    escaped = password.replace("'", "''")
    # S608 justified : password from trusted env (R20 + .env.local en dev /
    # secret manager en prod) ; CREATE ROLE PASSWORD ne supporte pas la
    # paramétrisation à l'intérieur d'un bloc DO/EXECUTE — l'interpolation
    # est inévitable. `escaped` neutralise les quotes pour rester un littéral
    # SQL valide. Le fichier vit dans alembic/ (exclu de ruff via pyproject)
    # mais on garde la justification écrite.
    create_role_sql = (
        "DO $$ BEGIN "  # noqa: S608 — escaped password from trusted env
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_read') "
        "THEN CREATE ROLE agent_read NOINHERIT LOGIN PASSWORD "
        f"'{escaped}'; END IF; END $$;"
    )
    op.execute(create_role_sql)

    # 2. statement_timeout = 5s (M6).
    op.execute("ALTER ROLE agent_read SET statement_timeout = '5s'")

    # 3. GRANT CONNECT + USAGE schema. La DB courante est lue dynamiquement
    # — la migration tourne en dev (ratis_dev), test (ratis_migration_test),
    # prod (ratis_prod). current_database() évite de hardcoder le nom.
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_read', current_database());
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO agent_read")

    # 4. REVOKE total sur les 7 tables interdites.
    for table in FORBIDDEN_TABLES:
        op.execute(f"REVOKE ALL ON public.{table} FROM agent_read")

    # 5. REVOKE colonne-par-colonne sur les tables sensibles, puis GRANT
    # SELECT sur le complément (colonnes safe). On lit les colonnes via
    # information_schema à l'instant T — toute colonne ajoutée plus tard
    # devra explicitement passer par une nouvelle migration qui décide
    # REVOKE ou GRANT (pas de magie « auto-GRANT au nouveau champ »).
    bind = op.get_bind()
    for table, sensitive_cols in COLUMN_SCOPED.items():
        op.execute(f"REVOKE ALL ON public.{table} FROM agent_read")
        cols = [
            r.column_name
            for r in bind.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t "
                    "ORDER BY ordinal_position"
                ),
                {"t": table},
            ).fetchall()
        ]
        safe_cols = [c for c in cols if c not in sensitive_cols]
        if not safe_cols:
            raise RuntimeError(
                f"HSP4 migration: aucune colonne safe pour {table} — "
                f"vérifie que les colonnes sensitive_cols={sensitive_cols!r} "
                f"correspondent au schéma réel (colonnes trouvées : {cols!r})"
            )
        col_list = ", ".join(safe_cols)
        op.execute(f"GRANT SELECT ({col_list}) ON public.{table} TO agent_read")

    # 6. GRANT SELECT plein sur le reste des tables public, en EXCLUANT
    # explicitement les 7 interdites et les 3 column-scoped (déjà traitées).
    excluded = set(FORBIDDEN_TABLES) | set(COLUMN_SCOPED.keys())
    open_tables = [
        r.table_name
        for r in bind.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' "
                "ORDER BY table_name"
            )
        ).fetchall()
    ]
    for table in open_tables:
        if table in excluded:
            continue
        op.execute(f"GRANT SELECT ON public.{table} TO agent_read")


def downgrade() -> None:
    # Symétrique. DROP OWNED BY agent_read révoque tous les privileges
    # accordés au rôle dans la DB courante. DROP ROLE termine.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_read') THEN
                DROP OWNED BY agent_read;
                DROP ROLE agent_read;
            END IF;
        END
        $$;
        """
    )
