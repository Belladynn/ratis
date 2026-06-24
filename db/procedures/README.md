# db/procedures/ — procédures stockées support

Un fichier `.sql` canonique par procédure (`support_<verbe>_<objet>.sql`).
**git est la source de vérité.** Les procédures sont appliquées à la base via
des migrations Alembic (helper `ratis_core.db_procedures.apply_procedure`).

Convention complète : `docs/superpowers/specs/2026-05-18-db-procedures-sp1-design.md`.
Gabarit du contrat : `_TEMPLATE.sql` (ignoré — préfixe `_`).
Catalogue auto-généré : `docs/arch/PROCEDURES.md`.

SP1 ne livre aucune procédure métier — `db/procedures/` ne contient que ce
README et le gabarit.
