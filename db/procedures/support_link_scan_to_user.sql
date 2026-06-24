-- support_link_scan_to_user.sql — lie un scan orphelin a un user (support).
--
-- HSP1 — atome facing, regime non_money. Cf manifeste sidecar
-- support_link_scan_to_user.manifest.toml. Aucun side-effect monetaire :
-- la re-attribution scan -> user est un acte de correction de donnees,
-- pas une operation de balance.

CREATE OR REPLACE PROCEDURE support_link_scan_to_user(
    IN  p_scan_id      uuid,
    IN  p_user_id      uuid,
    OUT rows_affected  integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE scans
    SET user_id = p_user_id
    WHERE id = p_scan_id;

    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_link_scan_to_user(uuid, uuid, integer)
    IS 'Lie un scan orphelin a un user (regime non_money, facing).';
