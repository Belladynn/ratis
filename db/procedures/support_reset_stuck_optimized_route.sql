-- support_reset_stuck_optimized_route.sql — reset stuck 'computing' optimized_routes for a list (support).
--
-- HSP1 — atome facing, regime non_money. Cf manifeste sidecar
-- support_reset_stuck_optimized_route.manifest.toml. Aucun side-effect monetaire :
-- on flip simplement status='computing' -> 'failed' pour debloquer le user vis-a-vis
-- du partial unique index uq_optimized_routes_one_computing_per_list. Le rescue
-- automatique (cf routes/optimization.py § ghost-row detection) couvre 99% des cas ;
-- cette procedure reste le filet de support manuel quand le seuil n'est pas atteint
-- ou qu'un operateur veut purger immediatement.

CREATE OR REPLACE PROCEDURE support_reset_stuck_optimized_route(
    IN  p_list_id      uuid,
    OUT rows_affected  integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE optimized_routes
    SET status = 'failed'
    WHERE list_id = p_list_id
      AND status = 'computing';

    GET DIAGNOSTICS rows_affected = ROW_COUNT;
    RAISE NOTICE 'support_reset_stuck_optimized_route: list_id=% rows_affected=%',
        p_list_id, rows_affected;
END;
$$;

COMMENT ON PROCEDURE support_reset_stuck_optimized_route(uuid, integer)
    IS 'Reset stuck computing optimized_routes for a list (regime non_money, facing).';
