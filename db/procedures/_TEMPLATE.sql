-- _TEMPLATE.sql — gabarit d'une procédure stockée support.
--
-- COPIER ce fichier sous db/procedures/support_<verbe>_<objet>.sql et adapter.
-- Ce fichier-ci est IGNORÉ (préfixe `_`) : ni catalogué, ni appliqué.
--
-- Contrat (cf. docs/superpowers/specs/2026-05-18-db-procedures-sp1-design.md) :
--   * nom préfixé `support_`
--   * déclarée CREATE OR REPLACE PROCEDURE (application idempotente)
--   * paramètre OUT rows_affected integer, alimenté par GET DIAGNOSTICS
--   * COMMENT ON PROCEDURE obligatoire — une ligne, extraite par le catalogue

CREATE OR REPLACE PROCEDURE support_example_action(
    IN  target_id     bigint,
    IN  delta         integer,
    OUT rows_affected integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- ... l'écriture support ...
    -- UPDATE some_table SET col = col + delta WHERE id = target_id;
    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_example_action(bigint, integer, integer)
    IS 'Gabarit — décrire ici en une ligne ce que fait la procédure.';
