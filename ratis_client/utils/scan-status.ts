// utils/scan-status.ts
//
// Canonical mapping `scan.status` × `scan.match_method` × `consensus_state`
// → UX (label, colour, reason-line presence) for the scan-history screen.
//
// Pipeline-v3 (rolled out 2026-04-30) writes new values to scans.status :
//   - `matched`     replaces `accepted`
//   - `unresolved`  is brand new — pipeline ran but couldn't link, the row
//                   carries a `rejected_reason` describing why
//   - `rejected`    same name but `rejected_reason` is mandatory
// and a refined `match_method` enum :
//   - `barcode` (was `barcode_ean`)
//   - `knowledge` (was `observed_name`/learned)
//   - `fuzzy_strict` (was `fuzzy`)
//   - `manual_admin` (was `manual`)
//   - `fuzzy_pending` (NRC — name-resolution pending crowdsourced consensus)
//   - `observed_name` (NRC — also routed through consensus)
//
// During the v2→v3 transition, rows of both shapes coexist in the DB. This
// module normalises both into the same UX vocabulary so the renderer stays
// dumb. Callers receive i18n keys (not raw strings) — components resolve via
// `t(key)` to keep the strings out of the codebase (R33).
//
// **Philosophie « vert = consensus only » (2026-05-01)** : la grosse pastille
// ne devient verte que pour les actes d'autorité explicite (barcode user,
// manual_admin) OU pour les matches automatiques (fuzzy/observed_name/...)
// validés par le consensus crowdsourcé (`consensus_state='verified'`). Tout
// autre match auto reste orange tant que la communauté ne l'a pas confirmé.
// Voir brief 2026-05-01 « fix(scan-history): grosse pastille verte uniquement
// si consensus crowdsourcé ».
//
// `formatRejectedReason` translates the backend's snake_case reason codes
// into a key the i18n layer can resolve. The `fuzzy_below_*_<score>` family
// is parsed to surface the score in the user-facing label.

export type ScanStatusV3 =
  | 'pending'
  | 'matched'
  | 'unresolved'
  | 'rejected'
  // legacy v2 (kept for backward-compat while DB still holds old rows)
  | 'accepted'
  | 'unmatched';

export type MatchMethodV3 =
  | 'barcode'
  | 'knowledge'
  | 'fuzzy_strict'
  | 'manual_admin'
  // NRC (name-resolution-consensus)
  | 'fuzzy_pending'
  | 'observed_name'
  // legacy v2
  | 'barcode_ean'
  | 'fuzzy'
  | 'fuzzy_confirmed'
  | 'manual';

/** Live consensus state — duplicates the union exported by
 *  ``hooks/use-receipt-items.ts`` to avoid a circular runtime dep (utils →
 *  hooks). Keep in sync. */
export type ConsensusStateForUx =
  | 'verified'
  | 'unverified'
  | 'controverse'
  | 'pending'
  | 'unresolved';

export type ScanUxColor = 'green' | 'green-dimmer' | 'orange' | 'red' | 'gray';

export interface ScanUx {
  /** i18n key resolving to the user-facing status label. */
  labelKey: string;
  color: ScanUxColor;
  /** When true, the renderer should also show `formatRejectedReason(reason)`
   *  as a sub-line. Only set for unresolved/rejected statuses. */
  hasReason: boolean;
}

const PENDING_UX: ScanUx = {
  labelKey: 'scan.status.pending',
  color: 'gray',
  hasReason: false,
};

const UNRESOLVED_UX: ScanUx = {
  labelKey: 'scan.status.unresolved',
  color: 'orange',
  hasReason: true,
};

const REJECTED_UX: ScanUx = {
  labelKey: 'scan.status.rejected',
  color: 'red',
  hasReason: true,
};

/** Methods qualifying as "user/admin authority" — the user explicitly scanned
 *  a barcode (unique numeric identifier, near-zero ambiguity) OR an admin
 *  validated the row by hand. These bypass consensus gating because they
 *  represent an explicit human action, not an automated guess. */
function isAuthorityMethod(method: MatchMethodV3 | null | string): boolean {
  return (
    method === 'barcode' ||
    method === 'barcode_ean' ||
    method === 'manual_admin' ||
    method === 'manual'
  );
}

/** Build the matched UX (label + colour) based on `match_method` and the
 *  live `consensus_state`.
 *
 *  Philosophy « vert = consensus only » :
 *  - Authority methods (barcode / manual_admin) → always green
 *    (manual_admin uses the dimmer green-dimmer to flag "admin-validated,
 *    not auto-detected").
 *  - Auto methods (fuzzy_pending, observed_name, knowledge, fuzzy_strict,
 *    fuzzy, fuzzy_confirmed, null/unknown) → green ONLY when consensus has
 *    been validated by the community (`consensus_state='verified'`). Any
 *    other state — `unverified`, `controverse`, `pending`, `unresolved`,
 *    `null`, or absent — defaults to **orange** (not validated yet ⇒ we do
 *    not claim "Reconnu" status). */
function matchedUx(
  method: MatchMethodV3 | null | string,
  consensusState: ConsensusStateForUx | null | undefined,
): ScanUx {
  // 1. Authority methods bypass consensus gating.
  if (method === 'barcode' || method === 'barcode_ean') {
    return { labelKey: 'scan.status.matched_barcode', color: 'green', hasReason: false };
  }
  if (method === 'manual_admin' || method === 'manual') {
    // Manual review is trustworthy but visually de-emphasised so the user
    // notices "this was admin-validated, not auto-detected".
    return {
      labelKey: 'scan.status.matched_manual_admin',
      color: 'green-dimmer',
      hasReason: false,
    };
  }

  // 2. Non-authority methods: green only if consensus is verified.
  //    Default = orange (safe), since we never claim "Reconnu" without
  //    crowdsourced confirmation for an automated guess.
  const verified = consensusState === 'verified';

  if (method === 'knowledge' || method === 'observed_name') {
    return verified
      ? { labelKey: 'scan.status.matched_knowledge', color: 'green', hasReason: false }
      : { labelKey: 'scan.status.matched_pending_consensus', color: 'orange', hasReason: false };
  }
  // fuzzy_pending, fuzzy_strict, fuzzy, fuzzy_confirmed, null, unknown
  return verified
    ? { labelKey: 'scan.status.matched_fuzzy_strict', color: 'green', hasReason: false }
    : { labelKey: 'scan.status.matched_pending_consensus', color: 'orange', hasReason: false };
}

/** Map a (status, match_method, consensus_state) tuple to its UX
 *  representation. Accepts both v3 and v2 values transparently. Unknown
 *  statuses fall back to the pending UX (safe default — gray, no reason).
 *
 *  `consensusState` is optional for backward-compat with older callers /
 *  tests, but the new "vert = consensus only" rule means callers SHOULD pass
 *  it whenever the backend exposes the field — otherwise non-authority
 *  matches stay orange (safe default). */
export function mapStatusToUx(
  status: ScanStatusV3 | string,
  matchMethod: MatchMethodV3 | string | null,
  consensusState?: ConsensusStateForUx | null,
): ScanUx {
  switch (status) {
    case 'matched':
    case 'accepted':
      return matchedUx(matchMethod, consensusState);
    case 'unresolved':
    case 'unmatched':
      return UNRESOLVED_UX;
    case 'rejected':
      return REJECTED_UX;
    case 'pending':
      return PENDING_UX;
    default:
      return PENDING_UX;
  }
}

// Re-exported for tests / callers that need to inspect the rule.
export { isAuthorityMethod };

// -- Rejected-reason translation ------------------------------------------

const KNOWN_REASONS = new Set<string>([
  'no_fuzzy_candidate',
  'barcode_unknown_in_db',
  'parsing_issue',
  'no_qty',
  'no_price',
  'ocr_garbage',
  'duplicate_receipt',
  'fuzzy_below_threshold',
]);

/** Pipeline-v3 emits scored variants like ``fuzzy_below_threshold_0.654`` or
 *  ``fuzzy_below_auto_accept_0.812``. We surface the score (truncated to 2
 *  decimals) inline with the label, so the user sees *why* the match was
 *  borderline. Returns a "key|score" tuple the i18n layer interpolates. */
const FUZZY_SCORE_RE = /^fuzzy_below_(?:threshold|auto_accept)_(\d+(?:\.\d+)?)$/;

/** Translate a backend reason code into the i18n key the UI should resolve.
 *  Returns ``"<key>|<score>"`` for the scored fuzzy variants — callers split
 *  on `|` and pass the score as an interpolation parameter. */
export function formatRejectedReason(reason: string | null | undefined): string {
  if (!reason) return 'scan.rejected_reason.default';

  const fuzzyMatch = reason.match(FUZZY_SCORE_RE);
  if (fuzzyMatch) {
    const score = parseFloat(fuzzyMatch[1]);
    // Truncate to 2 decimals — the backend may emit 3+ decimals from the
    // raw similarity score, but the UI only needs a coarse hint.
    const display = Number.isFinite(score) ? score.toFixed(2) : fuzzyMatch[1];
    return `scan.rejected_reason.fuzzy_below_threshold_score|${display}`;
  }

  if (KNOWN_REASONS.has(reason)) {
    return `scan.rejected_reason.${reason}`;
  }
  return 'scan.rejected_reason.default';
}
