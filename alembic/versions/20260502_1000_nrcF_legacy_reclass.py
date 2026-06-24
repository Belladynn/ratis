"""NRC bloc F — reclass legacy fuzzy_strict scans + backfill ledger from barcode + deprecate observed_names view.

Revision ID: 20260502_1000_nrcF
Revises: 20260501_2000_nrcD
Create Date: 2026-05-02 10:00:00

Three concerns, one migration (data-only, no schema change beyond a single
``COMMENT ON VIEW``) :

1. **Reclass legacy ``fuzzy_strict`` scans** — pipeline_v3 (PR #199) and
   earlier promoted any fuzzy hit at score >= ``fuzzy_auto_accept`` to
   ``status='matched'`` / ``match_method='fuzzy_strict'`` *without*
   crowdsourced consensus. Per ARCH § "Genèse" the green icon (matched
   verified) must come from the consensus ledger, never from fuzzy alone.
   We move every legacy ``fuzzy_strict`` row from ``matched`` back to
   ``pending`` (BDD-state, not user-facing — see CLAUDE.md ``scans``
   semantics). The scanner originel keeps their resolved view because the
   ``product_ean`` / ``match_method`` columns stay populated ; only the
   pipeline lifecycle flag changes so the row will be re-evaluated by the
   matcher cascade once the consensus ledger is hot.

   **Decision — exclude ``status='accepted'``** : the trigger
   ``fn_check_scan_status_transition`` (BEFORE UPDATE OF status on scans)
   raises ``Forbidden transition: an accepted scan cannot change status``
   on any move out of ``accepted``. That trigger is a load-bearing
   user-facing invariant : ``accepted`` scans are user-confirmed and
   appear in cashback ledgers / receipt history ; flipping one back to
   ``pending`` would corrupt downstream materialised views. The original
   brief enumerated ``('matched', 'accepted')`` ; we narrow to
   ``('matched',)`` and document the rationale here. Pipeline_v3
   never produces ``accepted`` directly anyway — that status is only
   reachable through the user-confirm flow which already runs through
   the trigger and would have rejected a fuzzy_strict promotion.
   Disabling the trigger to bypass the rule would be a workaround
   (against R33) ; we take the clean path and leave ``accepted`` rows
   untouched.

   Idempotent : the WHERE clause filters out rows already moved by a
   prior run.

2. **Backfill the consensus ledger from historic ``barcode`` scans** — every
   scan with ``match_method='barcode'`` is, by definition, a high-confidence
   user validation of the ``(store_id, scanned_name) → product_ean``
   triplet. We seed ``product_name_resolutions`` with those rows so the
   consensus state machine starts with real data instead of a cold cache.

   Normalize approach (DECISION) : the matcher uses
   ``worker.pipeline.matcher._normalize_text`` which performs *runtime*
   ``ocr_knowledge`` lookups (DB round-trips that mutate the knowledge
   table) and is therefore **not portable to SQL**. We could call it from
   Python inside the migration, but doing so during an Alembic upgrade
   would (a) couple the migration to live ``ocr_knowledge`` state — fragile
   in CI ; (b) write to ``ocr_knowledge`` as a side-effect, which a
   migration must never do ; (c) take O(N) DB round-trips per scan,
   prohibitive at any reasonable scale.

   We pick the **simpler, deterministic alternative** : use ``UPPER(TRIM(
   scanned_name))`` as the ``normalized_label``. Same fold the legacy
   ``_lookup_observed`` view applied (``UPPER(scanned_name)`` join) and
   the same fold the existing matcher uses when comparing ledger rows
   in ``get_consensus_for_label`` (the lookup key matches the produced
   ``MatchResult.normalized_label`` *after* knowledge correction). For
   barcode-scanned rows specifically this is a sound approximation —
   barcode scans bypass OCR knowledge correction in the live pipeline
   (``services/barcode_service`` uses the raw scanned name). When the
   live cascade later writes a corrected label for the same triplet,
   the UNIQUE ``(scan_id, normalized_label)`` index keys on a different
   string and we get *both* validation rows in the ledger — strictly
   additive, never destructive.

   See ARCH § "Migration `product_observed_names`" for the long-form
   discussion. Trade-off : we lose the small win of having uppercase-
   uniformised legacy labels collide with the future corrected ones.
   Acceptable at V1 — the ledger drift will burn off naturally as new
   scans land.

   Idempotent : ``ON CONFLICT (scan_id, normalized_label) DO NOTHING``
   (unique index ``idx_pnr_scan_label``).

3. **Deprecate the ``product_observed_names`` view** — superseded by the
   ``product_name_resolutions`` ledger which carries crowdsourced
   evidence with explicit method discriminators. We do **not** drop the
   view (V2 scope, post-bêta) — only attach a ``COMMENT ON VIEW`` so any
   reader (psql ``\\d+``, pgAdmin) sees the deprecation notice. Reads
   from the matcher cascade Step 3 stay legal until bloc F's V2 sweep
   replaces the SELECT path.

Order of operations matters : (1) before (2). Reclassing fuzzy_strict
matched→pending does not affect the barcode backfill (which filters on
``match_method='barcode'``), but doing the reclass first leaves the DB
in a coherent v3-vocabulary state before we land ledger rows.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "20260502_1000_nrcF"
down_revision = "20260501_2000_nrcD"
branch_labels = None
depends_on = None


# ── (1) reclass legacy fuzzy_strict ───────────────────────────────────────────
_RECLASS_FUZZY_STRICT_SQL = """
    UPDATE scans
       SET status = 'pending'
     WHERE status = 'matched'
       AND match_method = 'fuzzy_strict'
"""

# ── (2) backfill ledger from barcode scans ────────────────────────────────────
# We INSERT-SELECT in a single statement using ``UPPER(TRIM(scanned_name))``
# as the deterministic fallback ``normalized_label``. ON CONFLICT keys on the
# unique index ``idx_pnr_scan_label = (scan_id, normalized_label)``.
#
# We filter on :
#   - match_method = 'barcode' : the source-of-truth signal for this backfill.
#   - store_id IS NOT NULL : NRC consensus is per-store ; null-store scans are
#     historic data we cannot reliably bind to a (store, label) pair.
#   - scanned_name IS NOT NULL : ``normalized_label`` is NOT NULL in the
#     ledger schema.
#   - product_ean IS NOT NULL : same — the ledger row carries the resolved EAN.
#   - user_id IS NOT NULL : RGPD-anonymised scans have user_id NULL ; the
#     ledger schema requires a user FK so we cannot safely seed those rows.
#
# Whitespace and accent handling : we ``TRIM`` to drop trailing spaces from
# OCR but do NOT strip accents — the live cascade preserves them, so should
# we.
_BACKFILL_BARCODE_LEDGER_SQL = """
    INSERT INTO product_name_resolutions
        (id, scan_id, store_id, normalized_label, product_ean,
         user_id, match_method, resolved_at)
    SELECT
        gen_random_uuid(),
        s.id,
        s.store_id,
        UPPER(TRIM(s.scanned_name)) AS normalized_label,
        s.product_ean,
        s.user_id,
        'barcode',
        s.scanned_at
    FROM scans s
    WHERE s.match_method = 'barcode'
      AND s.store_id IS NOT NULL
      AND s.scanned_name IS NOT NULL
      AND TRIM(s.scanned_name) <> ''
      AND s.product_ean IS NOT NULL
      AND s.user_id IS NOT NULL
    ON CONFLICT (scan_id, normalized_label) DO NOTHING
"""

# ── (3) deprecate observed_names view ─────────────────────────────────────────
_DEPRECATE_OBSERVED_NAMES_SQL = (
    "COMMENT ON VIEW product_observed_names IS "
    "'DEPRECATED — use product_name_resolutions (NRC bloc A+). "
    "Read-only after 2026-05-02 ; physical drop scheduled for V2 post-bêta.'"
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text(_RECLASS_FUZZY_STRICT_SQL))
    bind.execute(text(_BACKFILL_BARCODE_LEDGER_SQL))
    bind.execute(text(_DEPRECATE_OBSERVED_NAMES_SQL))


def downgrade() -> None:
    """Best-effort downgrade.

    - The view comment is restored to ``NULL`` (Postgres default for views
      without an explicit COMMENT). This is the only schema-level change
      and it is exactly reversible.
    - We do **not** restore ``status='matched'`` for the reclassed scans :
      the upgrade discarded the per-scan distinction between original
      ``matched`` and ``accepted`` (both folded to ``pending``) ; without
      a snapshot we cannot reconstruct the pre-upgrade state. Operators
      who need a true rollback must restore from a logical backup.
    - We do **not** delete the seeded ledger rows : they are valid
      validations whose only sin is having been entered in bulk. Deleting
      them would corrupt the consensus state of any label that subsequent
      scans have built on top.

    These trade-offs are documented here so a downgrade reader is not
    surprised by the asymmetry.
    """
    op.execute("COMMENT ON VIEW product_observed_names IS NULL")
