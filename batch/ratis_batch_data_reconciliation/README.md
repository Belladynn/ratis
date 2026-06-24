# ratis_batch_data_reconciliation

Réconciliation métier (data, pas argent) — séparé du batch financier
`ratis_batch_reconciliation`.

Voir [ARCH_BATCH_DATA_RECONCILIATION.md](./ARCH_BATCH_DATA_RECONCILIATION.md)
pour la genèse, le scope et le plan d'implémentation par phases.

## Jobs

Phase 1 (this PR) :

1. **`ean_recovery`** — re-balaye les scans `unresolved` récents et retry
   le matching via les repos NRC retailer-keyed. Profite des consensus
   accumulés depuis la dernière run.
2. **`store_mdd_vote`** — STUB Phase 2 (no-op + warning log).
3. **`price_disambiguate`** — STUB Phase 2 (no-op + warning log).
4. **`retro_cab`** — crédite CAB rétroactif (`reference_type='retro_scan'`)
   sur les scans nouvellement matched + agrège par user + déclenche notif
   gratitude (`type='retro_cab_gratitude'`).

Les 4 jobs tournent séquentiellement dans `run.py`. Si un job plante, les
autres tournent quand même (try/except per-job).

## Commandes

```bash
# Run normal (live, écrit en DB + déclenche notifs)
uv run --package ratis-batch-data-reconciliation python batch/ratis_batch_data_reconciliation/run.py

# Dry-run (logs + détection only, no DB writes, no notif)
uv run --package ratis-batch-data-reconciliation python batch/ratis_batch_data_reconciliation/run.py --dry-run

# Tests
uv run --package ratis-batch-data-reconciliation pytest batch/ratis_batch_data_reconciliation/tests/ -q
```

## Variables d'environnement

| Var | Requise | Usage |
|---|---|---|
| `DATABASE_URL` | oui | `postgresql+psycopg://...` (jamais `postgresql://`) |
| `NOTIFIER_URL` | oui (Job 4) | URL de `ratis_notifier` (ex `http://localhost:8005/api/v1/notify`) |
| `INTERNAL_API_KEY` | oui (Job 4) | clé Bearer partagée avec NT |

Job 4 skip clean (log error) si `NOTIFIER_URL` ou `INTERNAL_API_KEY`
manquent ; les autres jobs continuent.

## Schedule

Cron prévu : `0 6 * * *` (06:00 UTC quotidien — après off_sync 04:00).

Workflow GH Action `.github/workflows/batch_data_reconciliation.yml` —
schedule désactivé tant que la DB prod n'est pas câblée (cf
ARCH_deployment.md § "Stratégie hébergement"). Trigger manuel via
`workflow_dispatch` pour debug.
