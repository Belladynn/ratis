"""HSP2 — plancher BDD : db_change_log + caps dormants.

Revision ID: 20260520_1000_hsp2_floor
Revises: 20260519_1000_initial_atoms
Create Date: 2026-05-20 10:00:00.000000

Voir :
    ARCH_n8n_pipelines.md § HSP-2 (plancher BDD — décisions consolidées post-merge ; spec d'origine récupérable via git history)

Cette migration installe :
1. La table ``db_change_log`` (append-only — UPDATE/DELETE bloques par trigger).
2. Le trigger generique ``fn_db_change_log_record()`` attache aux 6 tables
   sensibles (Task 3 ajoutera la fonction + les 6 CREATE TRIGGER).
3. Le trigger de caps temporels ``fn_db_pipeline_caps_enforce()`` dormant
   sur ``cabecoin_transactions`` (Task 4 ajoutera la fonction + le trigger
   + l'index ``ix_cabecoin_tx_user_created`` + le seed app_settings).

Tout est dormant tant que :
- aucune connexion n'a fait ``SET LOCAL app.submission_id``
  (=> submission_id reste NULL dans le log) ;
- aucune connexion n'a fait ``SET LOCAL app.caps_enforced = 'true'``
  ET ``app_settings.db_pipeline_caps.caps_enforced`` n'est pas ``true``
  (=> trigger des caps no-op).

Voir aussi : NOTE futur HSP4/HSP5 — un role agent restreint devra avoir
    REVOKE SELECT ON db_change_log FROM agent_role;
    REVOKE INSERT, UPDATE, DELETE ON app_settings FROM agent_role;
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers (≤32 chars per R08 — 28 chars).
revision = "20260520_1000_hsp2_floor"
down_revision = "20260519_1000_initial_atoms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. db_change_log : table append-only ────────────────────────────────
    op.create_table(
        "db_change_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("old_data", postgresql.JSONB(), nullable=True),
        sa.Column("new_data", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "op IN ('insert', 'update', 'delete')",
            name="db_change_log_op_check",
        ),
    )

    # Partial index : only rows with a submission_id (pipeline-produced).
    op.execute(
        """
        CREATE INDEX idx_db_change_log_submission
        ON db_change_log (submission_id, created_at)
        WHERE submission_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_db_change_log_table_time
        ON db_change_log (table_name, created_at)
        """
    )

    # ── 2. Guards append-only : pas d'UPDATE, pas de DELETE ────────────────
    # Pattern reuse direct de fn_pipeline_audit_log_no_update (migration
    # 20260430_1000_pipeline_v3_clean.py). On garde un message d'erreur
    # qui mentionne "append-only" pour que les tests puissent matcher.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_db_change_log_no_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'db_change_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_db_change_log_no_update
        BEFORE UPDATE ON db_change_log
        FOR EACH ROW EXECUTE FUNCTION fn_db_change_log_no_update();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_db_change_log_no_delete
        BEFORE DELETE ON db_change_log
        FOR EACH ROW EXECUTE FUNCTION fn_db_change_log_no_update();
        """
    )

    # ── 3. Trigger generique : capture to_jsonb(OLD) / to_jsonb(NEW) ────────
    # Pose une ligne dans db_change_log par row touchee sur les 6 tables
    # sensibles. submission_id est lu via current_setting('app.submission_id',
    # true) — true = "missing_ok" : retourne '' si la setting n'a pas ete
    # posee dans la transaction, qu'on NULLIF en NULL.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_db_change_log_record()
        RETURNS trigger AS $$
        DECLARE
            v_sub_text  text := current_setting('app.submission_id', true);
            v_sub       uuid := NULLIF(v_sub_text, '')::uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO db_change_log
                    (submission_id, table_name, op, old_data, new_data)
                VALUES
                    (v_sub, TG_TABLE_NAME, 'insert', NULL, to_jsonb(NEW));
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO db_change_log
                    (submission_id, table_name, op, old_data, new_data)
                VALUES
                    (v_sub, TG_TABLE_NAME, 'update', to_jsonb(OLD), to_jsonb(NEW));
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                INSERT INTO db_change_log
                    (submission_id, table_name, op, old_data, new_data)
                VALUES
                    (v_sub, TG_TABLE_NAME, 'delete', to_jsonb(OLD), NULL);
                RETURN OLD;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Attach to the 6 sensitive tables. Trigger names follow the convention
    # ``trg_db_change_log_record_<table>`` — tested in
    # ``test_all_six_sensitive_tables_have_record_trigger``.
    for _t in (
        "user_cab_balance",
        "cabecoin_transactions",
        "cashback_transactions",
        "cashback_withdrawals",
        "subscriptions",
        "scans",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_db_change_log_record_{_t}
            AFTER INSERT OR UPDATE OR DELETE ON {_t}
            FOR EACH ROW EXECUTE FUNCTION fn_db_change_log_record();
            """
        )

    # ── 4. Caps temporels dormants sur cabecoin_transactions ────────────────
    # L'index (user_id, created_at) borne le SUM 24h sur cabecoin_transactions.
    # Sans lui, le trigger ferait un seq scan a chaque INSERT — couteux quand
    # la table grossit. Cree maintenant pour que les caps soient utilisables
    # en O(log n) des l'activation.
    op.execute(
        """
        CREATE INDEX ix_cabecoin_tx_user_created
        ON cabecoin_transactions (user_id, created_at)
        """
    )

    # Seed app_settings.db_pipeline_caps. caps_enforced=false par defaut
    # → mode dormant. L'activation est une operation explicite separee
    # (cf PROD_CHECKLIST.md § Activation V1.1 — actes humains requis).
    op.execute(
        """
        INSERT INTO app_settings (section, data) VALUES (
            'db_pipeline_caps',
            $$
            {
                "caps_enforced": false,
                "cab_global_daily_warn": 20000,
                "cab_global_daily_block": 50000,
                "cab_per_user_daily_block": 5000
            }
            $$::jsonb
        )
        """
    )

    # Fonction d'enforcement. Double kill-switch obligatoire :
    # - session : SET LOCAL app.caps_enforced = 'true' (pose par la pipeline)
    # - settings : app_settings.db_pipeline_caps.caps_enforced = true
    # Sans LES DEUX, no-op. Seul direction='credit' compte vers le plafond
    # (un debit ne contribue pas — on borne ce qui SORT vers les users).
    # RAISE WARNING n'avorte pas la transaction (notice-level) ; RAISE EXCEPTION
    # avorte.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_db_pipeline_caps_enforce()
        RETURNS trigger AS $$
        DECLARE
            v_session_enforced text := current_setting('app.caps_enforced', true);
            v_caps             jsonb;
            v_warn             bigint;
            v_block            bigint;
            v_user_block       bigint;
            v_today_global     bigint;
            v_today_user       bigint;
        BEGIN
            -- Direction-aware : un debit ne contribue jamais au plafond.
            IF NEW.direction IS DISTINCT FROM 'credit' THEN
                RETURN NEW;
            END IF;

            -- Kill-switch session : si la pipeline n'a pas pose app.caps_enforced,
            -- ne rien faire. Mode bootstrap intact.
            IF v_session_enforced IS DISTINCT FROM 'true' THEN
                RETURN NEW;
            END IF;

            -- Kill-switch settings : verifie que l'enforcement est aussi
            -- explicitement active en BDD.
            SELECT data INTO v_caps
            FROM app_settings WHERE section = 'db_pipeline_caps';
            IF v_caps IS NULL
               OR (v_caps->>'caps_enforced')::bool IS DISTINCT FROM true THEN
                RETURN NEW;
            END IF;

            v_warn       := COALESCE((v_caps->>'cab_global_daily_warn')::bigint,  20000);
            v_block      := COALESCE((v_caps->>'cab_global_daily_block')::bigint, 50000);
            v_user_block := COALESCE((v_caps->>'cab_per_user_daily_block')::bigint, 5000);

            -- Cumul global 24h (credit only).
            SELECT COALESCE(SUM(amount), 0) INTO v_today_global
            FROM cabecoin_transactions
            WHERE direction = 'credit'
              AND created_at > now() - interval '24 hours';

            IF v_today_global + NEW.amount > v_block THEN
                RAISE EXCEPTION
                    'db_pipeline_caps: global daily block exceeded (% + % > %)',
                    v_today_global, NEW.amount, v_block;
            END IF;
            IF v_today_global + NEW.amount > v_warn THEN
                RAISE WARNING
                    'db_pipeline_caps: global daily warn threshold crossed (% + % > %)',
                    v_today_global, NEW.amount, v_warn;
            END IF;

            -- Cumul per-user 24h (credit only).
            SELECT COALESCE(SUM(amount), 0) INTO v_today_user
            FROM cabecoin_transactions
            WHERE direction = 'credit'
              AND user_id = NEW.user_id
              AND created_at > now() - interval '24 hours';

            IF v_today_user + NEW.amount > v_user_block THEN
                RAISE EXCEPTION
                    'db_pipeline_caps: per-user daily block exceeded for user % (% + % > %)',
                    NEW.user_id, v_today_user, NEW.amount, v_user_block;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_db_pipeline_caps_enforce
        BEFORE INSERT ON cabecoin_transactions
        FOR EACH ROW EXECUTE FUNCTION fn_db_pipeline_caps_enforce();
        """
    )


def downgrade() -> None:
    # Symetrique. Les guards no-update / no-delete sont DROP via leur
    # fonction ; les triggers tombent en cascade quand on DROP la fonction
    # avec CASCADE, mais on les DROP explicitement pour la lisibilite.

    # Caps temporels (Task 4)
    op.execute("DROP TRIGGER IF EXISTS trg_db_pipeline_caps_enforce ON cabecoin_transactions")
    op.execute("DROP FUNCTION IF EXISTS fn_db_pipeline_caps_enforce()")
    op.execute("DELETE FROM app_settings WHERE section = 'db_pipeline_caps'")
    op.execute("DROP INDEX IF EXISTS ix_cabecoin_tx_user_created")

    # Detach the 6 record triggers + drop the function.
    for _t in (
        "user_cab_balance",
        "cabecoin_transactions",
        "cashback_transactions",
        "cashback_withdrawals",
        "subscriptions",
        "scans",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_db_change_log_record_{_t} ON {_t}")
    op.execute("DROP FUNCTION IF EXISTS fn_db_change_log_record()")

    op.execute("DROP TRIGGER IF EXISTS trg_db_change_log_no_delete ON db_change_log")
    op.execute("DROP TRIGGER IF EXISTS trg_db_change_log_no_update ON db_change_log")
    op.execute("DROP FUNCTION IF EXISTS fn_db_change_log_no_update()")

    op.execute("DROP INDEX IF EXISTS idx_db_change_log_table_time")
    op.execute("DROP INDEX IF EXISTS idx_db_change_log_submission")
    op.drop_table("db_change_log")
