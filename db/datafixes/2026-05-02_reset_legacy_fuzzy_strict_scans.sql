-- ============================================================
-- Reset legacy fuzzy_strict scans to unresolved (matcher consensus-only refonte)
-- ============================================================
-- Following the matcher consensus-only refonte (2026-05-02 — see
-- ``ARCH_name_resolution_consensus.md`` § "Philosophie") :
--
--   * The matcher no longer attempts product-level fuzzy matching against
--     ``products``. ``fuzzy_strict`` was the legacy auto-promotion path used by
--     pipeline_v3 (PR #199) — it auto-matched scans to potentially polluted
--     OFF data (e.g. 30+ generic "Hipro" entries).
--   * Per the refonte philosophy, every legacy ``fuzzy_strict`` resolution
--     is suspect. We reset those scans to ``unresolved`` so the UI displays
--     the cleaned ``scanned_name`` and the user can resolve via :
--       (a) physically scanning the barcode (= contributes to consensus)
--       (b) PR2 product-suggestion flow (TBD — separate brainstorm)
--
-- Scope :
--   * status IN ('matched', 'accepted', 'pending') — the lifecycle states
--     where a fuzzy_strict resolution can still be retracted.
--   * status='rejected' is preserved — those rejections are real OCR/data
--     failures, not matcher artifacts.
--
-- Note re ``status='accepted'`` vs alembic migration ``20260502_1000_nrcF`` :
-- The latter narrowed to ``status='matched'`` only because the trigger
-- ``fn_check_scan_status_transition`` blocks moves out of ``accepted``.
-- This datafix runs ad-hoc with elevated rights and an operator-controlled
-- trigger-disable window if/when needed for the few ``accepted`` rows that
-- carry ``fuzzy_strict``. Operators must verify there are no downstream
-- side-effects (cashback ledgers / receipt history) before disabling the
-- trigger ; in most environments the safer path is to leave ``accepted``
-- rows alone (already done by the alembic migration) and run THIS datafix
-- only against ``matched`` / ``pending``.
--
-- Usage : run on prod after upgrading to the consensus-only matcher.
--   psql $DATABASE_URL -f db/datafixes/2026-05-02_reset_legacy_fuzzy_strict_scans.sql
-- ============================================================

BEGIN;

UPDATE scans
SET status = 'unresolved',
    product_ean = NULL,
    match_method = NULL,
    rejected_reason = NULL
WHERE match_method = 'fuzzy_strict'
  AND status IN ('matched', 'pending');

-- Audit log : the ``datafix_logs`` table is shared with the stored
-- procedures in ``db/datafixes/datafixes.sql``. We log a manual entry
-- here so this script's run is visible alongside procedure invocations.
-- ``executed_by`` defaults to ``current_user`` ; operators running via
-- a service role should re-bind explicitly if traceability matters.
INSERT INTO datafix_logs (id, procedure, params)
VALUES (
    gen_random_uuid(),
    '2026-05-02_reset_legacy_fuzzy_strict_scans',
    '{"scope": "scans", "summary": "Reset fuzzy_strict matched/pending → unresolved (NRC consensus-only refonte)"}'::jsonb
);

COMMIT;
