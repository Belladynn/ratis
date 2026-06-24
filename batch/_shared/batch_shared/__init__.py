"""ratis-batch-shared — pure-function helpers shared across batch workers.

Currently exposes :

- ``store_consolidation`` : trust hierarchy + fuzzy match + upsert logic for
  multi-source store ingestion (OSM, SIRENE, Overture, admin, user_suggested).
- ``retailer_resolution`` : source-agnostic retailer lookup + auto-create
  (extracted from ``ratis_batch_osm_sync/normalize.py``, PR7 will switch
  OSM to import from here too).

Imports :

    from batch_shared.store_consolidation import (
        CandidateStore,
        TrustPriority,
        UpsertResult,
        apply_upsert,
        find_match,
        trust_priority,
    )

    from batch_shared.retailer_resolution import resolve_or_create_retailer
"""
