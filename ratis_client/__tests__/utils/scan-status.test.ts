// Tests for the canonical pipeline-v3 status → UX mapping. Covers both v3
// (matched/unresolved) and legacy v2 (accepted/unmatched) status values so
// the UI keeps rendering correctly during the transition window where rows
// of both shapes coexist in the DB.
//
// 2026-05-01 update — « vert = consensus only ». The grosse pastille only
// turns green for authority methods (barcode, manual_admin) OR for auto
// matches whose `consensus_state='verified'`. Non-authority matches without
// a verified consensus default to **orange**. See `utils/scan-status.ts`
// header comment.
//
// See `ratis_client/utils/scan-status.ts` and brief Bloc 9.

import {
  mapStatusToUx,
  formatRejectedReason,
  isAuthorityMethod,
} from '@/utils/scan-status';

describe('mapStatusToUx — v3 statuses (authority methods)', () => {
  it('matched + barcode → green regardless of consensus_state', () => {
    // Barcode = unique numeric ID, near-zero ambiguity, scanned by user.
    // Authority bypasses the consensus gate.
    for (const cs of [
      undefined,
      null,
      'verified',
      'unverified',
      'controverse',
      'pending',
      'unresolved',
    ] as const) {
      const ux = mapStatusToUx('matched', 'barcode', cs);
      expect(ux.color).toBe('green');
      expect(ux.labelKey).toBe('scan.status.matched_barcode');
      expect(ux.hasReason).toBe(false);
    }
  });

  it('matched + manual_admin → green-dimmer regardless of consensus_state', () => {
    // Admin validation is an explicit human authority decision.
    for (const cs of [
      undefined,
      null,
      'verified',
      'unverified',
      'pending',
    ] as const) {
      const ux = mapStatusToUx('matched', 'manual_admin', cs);
      expect(ux.color).toBe('green-dimmer');
      expect(ux.labelKey).toBe('scan.status.matched_manual_admin');
      expect(ux.hasReason).toBe(false);
    }
  });
});

describe('mapStatusToUx — v3 statuses (auto methods, consensus-gated)', () => {
  it('matched + knowledge + verified → green "Reconnu (mémoire OCR)"', () => {
    const ux = mapStatusToUx('matched', 'knowledge', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_knowledge');
    expect(ux.hasReason).toBe(false);
  });

  it('matched + knowledge + null → orange (consensus not verified yet)', () => {
    const ux = mapStatusToUx('matched', 'knowledge', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
    expect(ux.hasReason).toBe(false);
  });

  it('matched + observed_name + verified → green', () => {
    const ux = mapStatusToUx('matched', 'observed_name', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_knowledge');
  });

  it('matched + observed_name + null → orange', () => {
    const ux = mapStatusToUx('matched', 'observed_name', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('matched + fuzzy_pending + verified → green', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_pending', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_fuzzy_strict');
  });

  it('matched + fuzzy_pending + null → orange (regression — was green before)', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_pending', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('matched + fuzzy_pending + unverified → orange', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_pending', 'unverified');
    expect(ux.color).toBe('orange');
  });

  it('matched + fuzzy_pending + controverse → orange', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_pending', 'controverse');
    expect(ux.color).toBe('orange');
  });

  it('matched + fuzzy_pending + pending → orange (safe default)', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_pending', 'pending');
    expect(ux.color).toBe('orange');
  });

  it('matched + fuzzy_pending + unresolved → orange', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_pending', 'unresolved');
    expect(ux.color).toBe('orange');
  });

  it('matched + fuzzy_strict (legacy) + null → orange (pre-NRC scans)', () => {
    // Older scans persisted before the NRC rollout still hold `fuzzy_strict`
    // and have no consensus row → must default to orange now that the rule
    // is "vert = consensus only".
    const ux = mapStatusToUx('matched', 'fuzzy_strict', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('matched + fuzzy_strict + verified → green', () => {
    const ux = mapStatusToUx('matched', 'fuzzy_strict', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_fuzzy_strict');
  });
});

describe('mapStatusToUx — v3 statuses (non-matched)', () => {

  it('unresolved + null → orange "À confirmer" + hasReason', () => {
    const ux = mapStatusToUx('unresolved', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.unresolved');
    expect(ux.hasReason).toBe(true);
  });

  it('rejected + null → red "Rejeté" + hasReason', () => {
    const ux = mapStatusToUx('rejected', null);
    expect(ux.color).toBe('red');
    expect(ux.labelKey).toBe('scan.status.rejected');
    expect(ux.hasReason).toBe(true);
  });

  it('pending + null → gray "En cours…"', () => {
    const ux = mapStatusToUx('pending', null);
    expect(ux.color).toBe('gray');
    expect(ux.labelKey).toBe('scan.status.pending');
    expect(ux.hasReason).toBe(false);
  });
});

describe('mapStatusToUx — v2 backward compat', () => {
  it('accepted + barcode_ean → green like matched (authority)', () => {
    // Legacy v2 alias of `barcode` — authority method, bypasses consensus.
    const ux = mapStatusToUx('accepted', 'barcode_ean');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_barcode');
  });

  it('accepted + barcode_ean → green even when consensus_state is null', () => {
    const ux = mapStatusToUx('accepted', 'barcode_ean', null);
    expect(ux.color).toBe('green');
  });

  it('accepted + fuzzy + null → orange (rule update: was green pre-consensus)', () => {
    // Pre-2026-05-01 this returned green. New rule: non-authority methods
    // require `consensus_state='verified'` to render green. Legacy v2 fuzzy
    // rows have no consensus row → orange.
    const ux = mapStatusToUx('accepted', 'fuzzy', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('accepted + fuzzy + verified → green', () => {
    const ux = mapStatusToUx('accepted', 'fuzzy', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_fuzzy_strict');
  });

  it('accepted + manual → green-dimmer (admin authority)', () => {
    // `manual` = legacy alias of `manual_admin`, authority method.
    const ux = mapStatusToUx('accepted', 'manual');
    expect(ux.color).toBe('green-dimmer');
    expect(ux.labelKey).toBe('scan.status.matched_manual_admin');
  });

  it('accepted + observed_name + verified → green (knowledge-equivalent)', () => {
    const ux = mapStatusToUx('accepted', 'observed_name', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_knowledge');
  });

  it('accepted + observed_name + null → orange (consensus not verified)', () => {
    const ux = mapStatusToUx('accepted', 'observed_name', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('accepted + fuzzy_confirmed + null → orange (no consensus row)', () => {
    // Legacy v2 row, no consensus tracked → defaults orange.
    const ux = mapStatusToUx('accepted', 'fuzzy_confirmed', null);
    expect(ux.color).toBe('orange');
  });

  it('accepted + fuzzy_confirmed + verified → green', () => {
    const ux = mapStatusToUx('accepted', 'fuzzy_confirmed', 'verified');
    expect(ux.color).toBe('green');
  });

  it('accepted + null match_method + null consensus → orange (safe default)', () => {
    // Without a known match_method AND no verified consensus, we cannot
    // claim "Reconnu" — fall back to orange.
    const ux = mapStatusToUx('accepted', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('unmatched + null → orange "À confirmer" (same UX as unresolved)', () => {
    const ux = mapStatusToUx('unmatched', null);
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.unresolved');
    expect(ux.hasReason).toBe(true);
  });
});

describe('mapStatusToUx — defensive', () => {
  it('unknown status falls back to gray pending-like', () => {
    // Defensive fallback for unknown future status values (signature accepts
    // bare `string` to allow forward-compat without breaking the build).
    const ux = mapStatusToUx('weird_status', null);
    expect(ux.color).toBe('gray');
    expect(ux.labelKey).toBe('scan.status.pending');
  });

  it('matched + unknown match_method + null consensus → orange (safe default)', () => {
    // Pipelines may add new auto methods (e.g. `barcode_v2`) the client
    // predates. Without authority + without verified consensus → orange.
    const ux = mapStatusToUx('matched', 'some_future_method');
    expect(ux.color).toBe('orange');
    expect(ux.labelKey).toBe('scan.status.matched_pending_consensus');
  });

  it('matched + unknown match_method + verified → green (consensus trusts it)', () => {
    const ux = mapStatusToUx('matched', 'some_future_method', 'verified');
    expect(ux.color).toBe('green');
    expect(ux.labelKey).toBe('scan.status.matched_fuzzy_strict');
  });
});

describe('isAuthorityMethod', () => {
  it('returns true for barcode-family and manual-family methods', () => {
    expect(isAuthorityMethod('barcode')).toBe(true);
    expect(isAuthorityMethod('barcode_ean')).toBe(true);
    expect(isAuthorityMethod('manual_admin')).toBe(true);
    expect(isAuthorityMethod('manual')).toBe(true);
  });

  it('returns false for auto/fuzzy/knowledge methods', () => {
    expect(isAuthorityMethod('fuzzy_pending')).toBe(false);
    expect(isAuthorityMethod('fuzzy_strict')).toBe(false);
    expect(isAuthorityMethod('fuzzy')).toBe(false);
    expect(isAuthorityMethod('fuzzy_confirmed')).toBe(false);
    expect(isAuthorityMethod('knowledge')).toBe(false);
    expect(isAuthorityMethod('observed_name')).toBe(false);
    expect(isAuthorityMethod(null)).toBe(false);
    expect(isAuthorityMethod('some_unknown')).toBe(false);
  });
});

describe('formatRejectedReason', () => {
  it('returns default key when reason is null', () => {
    expect(formatRejectedReason(null)).toBe('scan.rejected_reason.default');
  });

  it('returns default key when reason is empty', () => {
    expect(formatRejectedReason('')).toBe('scan.rejected_reason.default');
  });

  it('maps no_fuzzy_candidate', () => {
    expect(formatRejectedReason('no_fuzzy_candidate')).toBe(
      'scan.rejected_reason.no_fuzzy_candidate',
    );
  });

  it('maps barcode_unknown_in_db', () => {
    expect(formatRejectedReason('barcode_unknown_in_db')).toBe(
      'scan.rejected_reason.barcode_unknown_in_db',
    );
  });

  it('maps parsing_issue', () => {
    expect(formatRejectedReason('parsing_issue')).toBe(
      'scan.rejected_reason.parsing_issue',
    );
  });

  it('maps no_qty', () => {
    expect(formatRejectedReason('no_qty')).toBe(
      'scan.rejected_reason.no_qty',
    );
  });

  it('maps no_price', () => {
    expect(formatRejectedReason('no_price')).toBe(
      'scan.rejected_reason.no_price',
    );
  });

  it('maps ocr_garbage', () => {
    expect(formatRejectedReason('ocr_garbage')).toBe(
      'scan.rejected_reason.ocr_garbage',
    );
  });

  it('maps duplicate_receipt', () => {
    expect(formatRejectedReason('duplicate_receipt')).toBe(
      'scan.rejected_reason.duplicate_receipt',
    );
  });

  it('maps fuzzy_below_threshold (no score) to base key', () => {
    expect(formatRejectedReason('fuzzy_below_threshold')).toBe(
      'scan.rejected_reason.fuzzy_below_threshold',
    );
  });

  it('extracts score from fuzzy_below_threshold_<score>', () => {
    expect(formatRejectedReason('fuzzy_below_threshold_0.654')).toBe(
      'scan.rejected_reason.fuzzy_below_threshold_score|0.65',
    );
  });

  it('extracts score from fuzzy_below_auto_accept_<score>', () => {
    expect(formatRejectedReason('fuzzy_below_auto_accept_0.812')).toBe(
      'scan.rejected_reason.fuzzy_below_threshold_score|0.81',
    );
  });

  it('falls back to default for unknown reason', () => {
    expect(formatRejectedReason('totally_unexpected_reason')).toBe(
      'scan.rejected_reason.default',
    );
  });
});
