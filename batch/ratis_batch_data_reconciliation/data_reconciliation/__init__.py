"""ratis_batch_data_reconciliation — réconciliation métier (data, pas argent).

See ``ARCH_BATCH_DATA_RECONCILIATION.md`` for the full scope.

Phase 1 (this package) ships :

- :func:`ean_recovery.reconcile_ean_recovery` — Job 1 (Bloc I NRC).
- :func:`retro_cab.reconcile_retro_cab` — Job 4 (retro CAB credit + notif).

Phase 2 stubs that exist for orchestration end-to-end :

- :func:`store_mdd_vote.reconcile_store_mdd_vote` — STUB.
- :func:`price_disambiguate.reconcile_price_disambiguate` — STUB.
"""
