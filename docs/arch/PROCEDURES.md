# Ratis — Catalogue des procédures stockées support

> **Auto-généré — ne pas éditer à la main.**
> Régénérer : `python scripts/generate-procedures-catalogue.py`
> CI vérifie la fraîcheur à chaque PR (`.github/workflows/doc-inventories.yml`).

| Procédure | Arguments | Facing | Tier | Direction | Tables affectées | Description |
|---|---|---|---|---|---|---|
| `support_credit_cab` | `IN p_user_id uuid, IN p_amount integer, OUT rows_affected integer` | ✓ | `cab` | `credit` | `cabecoin_transactions`, `user_cab_balance` | Credit support : ajoute N CAB au user (regime cab, facing). |
| `support_debit_cab` | `IN p_user_id uuid, IN p_amount integer, OUT rows_affected integer` | ✓ | `cab` | `debit` | `cabecoin_transactions`, `user_cab_balance` | Debit support : retire N CAB du user (regime cab, facing). |
| `support_link_scan_to_user` | `IN p_scan_id uuid, IN p_user_id uuid, OUT rows_affected integer` | ✓ | `non_money` | `link` | `scans` | Lie un scan orphelin a un user (regime non_money, facing). |
| `support_reset_stuck_optimized_route` | `IN p_list_id uuid, OUT rows_affected integer` | ✓ | `non_money` | `fix` | `optimized_routes` | Reset stuck computing optimized_routes for a list (regime non_money, facing). |

---

**Total : 4 procédure(s).**
