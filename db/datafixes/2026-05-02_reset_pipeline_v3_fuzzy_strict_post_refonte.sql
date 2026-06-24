-- Datafix : reset pipeline_v3 scans tagged fuzzy_strict between
-- 2026-05-01 17:00 (deployment of the consensus-only matcher refonte
-- in worker/pipeline/matcher.py — that PR did NOT touch pipeline_v3,
-- which is the actually-active path for receipt scans) and the
-- deployment of THIS PR (which actually refactored
-- worker/pipeline_v3/match.py).
--
-- Context : 2 user receipts were processed at 18:23 on 2026-05-01,
-- post-deployment of the previous (incorrect) refonte. They came back
-- with match_method='fuzzy_strict' and status='matched' on random Hipro
-- products — the exact failure mode the refonte was meant to kill.
-- The previous datafix (2026-05-02_reset_legacy_fuzzy_strict_scans.sql,
-- run at 17:00) covered scans created BEFORE 17:00. This one covers
-- the gap from 17:00 to whenever this PR ships.
--
-- Idempotent : the WHERE clause filters on
-- (match_method='fuzzy_strict' AND status='matched') so re-running
-- is a no-op once the rows are reset.

UPDATE scans
SET status = 'unresolved',
    product_ean = NULL,
    match_method = NULL,
    rejected_reason = 'legacy_pipeline_v3_fuzzy_strict_reset',
    status_updated_at = now()
WHERE match_method = 'fuzzy_strict'
  AND status = 'matched'
  AND scanned_at >= '2026-05-01 17:00:00';

-- Audit : count what we touched (run separately, not as part of
-- the migration — kept here for operator copy-paste).
-- SELECT COUNT(*) FROM scans
-- WHERE rejected_reason = 'legacy_pipeline_v3_fuzzy_strict_reset';
