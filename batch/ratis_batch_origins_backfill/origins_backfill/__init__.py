"""ratis_batch_origins_backfill — one-shot ETL filling ``products.origins_tags``.

Phase C-2 of the missions sprint. The forward path is handled by
``ratis_batch_off_sync`` — every nightly delta touches ``origins_tags``
for any product it re-fetches. This batch fills the gap for historical
rows where ``origins_tags IS NULL``.

Idempotent : re-runs skip rows where ``origins_tags IS NOT NULL``. Safe
to run as many times as needed (e.g. interrupted by network blip).
"""
