"""HSP3 — gate humain durci : colonne mode + seeds human_approval +
db_pipeline_trust_levels + sentinelle n8n_resume_secret.

Une seule migration pour HSP3. Aucune table nouvelle — uniquement :

1. Colonne ``mode`` sur ``db_write_approvals`` (CHECK in 'execute',
   'graduation') — distingue les propositions normales des propositions
   de graduation M5.
2. Seed ``app_settings.human_approval`` — placeholder ``secret_set=false``.
   Le secret est installé hors migration par
   ``scripts/init-human-approval-secret.py`` (acte humain explicite).
3. Seed ``app_settings.db_pipeline_trust_levels`` — JSONB avec les 3
   atomes HSP1 tous à ``manual``. La graduation est une décision humaine
   passée par le pipeline complet (cf design HSP3 §M5).
4. Seed ``app_settings.n8n_resume_secret`` — sentinelle ``set=false`` au
   départ. Le lifespan PA met la valeur à true au boot quand l'env var
   est présente (fail-fast côté admin si la sentinelle reste false en
   prod).

Aucun trigger, aucune fonction, aucun nouvel index — HSP3 vit côté
applicatif (PA + n8n). La migration est juste le canal d'install des
seeds runtime.

Revision ID stable : ``20260521_1000_hsp3_human_gate`` (24 chars, R08).

cf ARCH_n8n_pipelines.md § HSP-3 (gate humain durci M1-M5 — décisions consolidées post-merge ; spec+plan d'origine récupérables via git history)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260521_1000_hsp3_human_gate"
down_revision = "20260520_1000_hsp2_floor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Colonne ``mode`` sur ``db_write_approvals`` ────────────────────
    # 'execute' = proposition normale (effet métier).
    # 'graduation' = proposition qui modifie app_settings.db_pipeline_trust_levels
    # (cf design HSP3 §M5). La graduation passe par le même gate humain ; le
    # mode permet à la pipeline et à l'UI de la traiter spécifiquement
    # (rendu badge, route Execute différente).
    op.execute(
        """
        ALTER TABLE db_write_approvals
        ADD COLUMN mode text NOT NULL DEFAULT 'execute'
        """
    )
    op.execute(
        """
        ALTER TABLE db_write_approvals
        ADD CONSTRAINT db_write_approvals_mode_check
        CHECK (mode IN ('execute', 'graduation'))
        """
    )

    # ── 2. Seed app_settings.human_approval ──────────────────────────────
    # Placeholder : secret_set=false, argon2_hash=null. L'install du secret
    # se fait par scripts/init-human-approval-secret.py (acte humain) qui
    # mute cette ligne en UPDATE { secret_set: true, argon2_hash: "$argon2id$..." }.
    op.execute(
        """
        INSERT INTO app_settings (section, data) VALUES (
            'human_approval',
            $$
            {
                "secret_set": false,
                "argon2_hash": null
            }
            $$::jsonb
        )
        """
    )

    # ── 3. Seed app_settings.db_pipeline_trust_levels ────────────────────
    # JSONB { procedure_name: "manual"|"caps_only"|"frozen" }. Les 3
    # atomes HSP1 sont initialisés à "manual". Toute graduation passe par
    # une proposition mode="graduation" qui modifie cette JSONB (cf endpoint
    # POST /api/v1/admin/db-pipeline/apply-graduation, Task 8).
    op.execute(
        """
        INSERT INTO app_settings (section, data) VALUES (
            'db_pipeline_trust_levels',
            $$
            {
                "support_credit_cab": "manual",
                "support_debit_cab": "manual",
                "support_link_scan_to_user": "manual"
            }
            $$::jsonb
        )
        """
    )

    # ── 4. Sentinelle app_settings.n8n_resume_secret ─────────────────────
    # Le secret lui-même vit en env (N8N_RESUME_SECRET côté PA et côté
    # n8n). La sentinelle est mise à set=true par le lifespan PA quand
    # l'env est présente — ainsi un admin observe en SQL si le secret
    # est armé sans avoir besoin de l'env.
    op.execute(
        """
        INSERT INTO app_settings (section, data) VALUES (
            'n8n_resume_secret',
            $$
            { "set": false }
            $$::jsonb
        )
        """
    )


def downgrade() -> None:
    # Symétrique. Les seeds sont supprimés ; la colonne ``mode`` est dropée
    # avec son CHECK. Idempotent via IF EXISTS (R07).
    op.execute(
        """
        DELETE FROM app_settings
        WHERE section IN (
            'human_approval',
            'db_pipeline_trust_levels',
            'n8n_resume_secret'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE db_write_approvals
        DROP CONSTRAINT IF EXISTS db_write_approvals_mode_check
        """
    )
    op.execute(
        """
        ALTER TABLE db_write_approvals
        DROP COLUMN IF EXISTS mode
        """
    )
