-- support_debit_cab.sql — debite N CAB d'un user (support).
--
-- HSP1 — atome facing, regime cab. Procedure DISTINCTE de support_credit_cab
-- (un atome ne se modifie pas via un parametre de direction). Cf manifeste
-- sidecar support_debit_cab.manifest.toml.
--
-- La CHECK `balance_nn` sur user_cab_balance fait remonter une CheckViolation
-- si le debit met le solde negatif — c'est le garde-fou BDD attendu.

CREATE OR REPLACE PROCEDURE support_debit_cab(
    IN  p_user_id      uuid,
    IN  p_amount       integer,
    OUT rows_affected  integer
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE user_cab_balance
    SET balance = balance - p_amount
    WHERE user_id = p_user_id;

    INSERT INTO cabecoin_transactions (user_id, direction, amount, reason)
    VALUES (p_user_id, 'debit', p_amount, 'admin_adjustment');

    GET DIAGNOSTICS rows_affected = ROW_COUNT;
END;
$$;

COMMENT ON PROCEDURE support_debit_cab(uuid, integer, integer)
    IS 'Debit support : retire N CAB du user (regime cab, facing).';
