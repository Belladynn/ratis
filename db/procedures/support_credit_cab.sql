-- support_credit_cab.sql — credite N CAB a un user (support).
--
-- HSP1 — atome facing, regime cab. Cf manifeste sidecar
-- support_credit_cab.manifest.toml pour le contrat declaratif complet.

CREATE OR REPLACE PROCEDURE support_credit_cab(
    IN  p_user_id      uuid,
    IN  p_amount       integer,
    OUT rows_affected  integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE user_cab_balance
    SET balance = balance + p_amount
    WHERE user_id = p_user_id;

    INSERT INTO cabecoin_transactions (user_id, direction, amount, reason)
    VALUES (p_user_id, 'credit', p_amount, 'admin_adjustment');

    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_credit_cab(uuid, integer, integer)
    IS 'Credit support : ajoute N CAB au user (regime cab, facing).';
