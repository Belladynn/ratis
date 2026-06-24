// __tests__/utils/mission-labels.test.ts
//
// Guards the mission label mapping in `types/gamification.ts` against
// regressions when the backend mission catalogue is extended.
//
// The active templates seeded server-side (catalogue revision 2026-05-12)
// span 7 distinct `action_type` values. Each one MUST have a French label
// — otherwise the UI surfaces the raw `action_type` enum string as the
// mission title, which is what the post-deploy PO ticket flagged
// (`receipt_scan` rendered as "receipt_scan" instead of "Scanne un ticket
// de caisse").

import { ACTION_LABELS, getMissionLabel } from '@/types/gamification';

/**
 * Source of truth : the backend mission catalogue. Keep this list in sync
 * with the seeded templates (see `webservices/ratis_rewards` migrations and
 * the missions table). When the backend ships a new action_type, this test
 * fails and points the PO at the missing label.
 */
const ACTIVE_TEMPLATE_ACTION_TYPES = [
  'receipt_scan',
  'label_scan',
  'barcode_scan',
  'product_enrich',
  'product_identification',
  'scan_distinct',
  'promo_found',
  'fill_product_field',
  'referral',
] as const;

describe('mission action_type labels', () => {
  it.each(ACTIVE_TEMPLATE_ACTION_TYPES)(
    'has a French label for %s',
    (actionType) => {
      const label = getMissionLabel(actionType);
      expect(label).not.toBe(actionType);
      // Non-empty and starts with a capital letter — sanity check the entry
      // is human-readable, not an enum slug.
      expect(label.length).toBeGreaterThan(2);
      expect(/^[A-ZÀ-Ÿ]/.test(label)).toBe(true);
    },
  );

  it('falls back to the raw action_type for unknown values', () => {
    expect(getMissionLabel('mystery_new_type')).toBe('mystery_new_type');
  });

  it('uses imperative French wording (PO directive — verb-first)', () => {
    // The PO wants action-oriented copy ("Scanne ..." not "Scanner ...").
    // Spot-check a representative subset.
    expect(ACTION_LABELS.receipt_scan).toMatch(/^Scanne /);
    expect(ACTION_LABELS.product_identification).toMatch(/^Identifie /);
    expect(ACTION_LABELS.promo_found).toMatch(/^Trouve /);
  });
});
