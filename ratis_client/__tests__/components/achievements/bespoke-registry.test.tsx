// ratis_client/__tests__/components/achievements/bespoke-registry.test.tsx
//
// Achievements V1 — bespoke registry contract test (PR 8/8).
//
// We don't deeply render the bespoke components themselves (they are
// "polished placeholders" for V1 — visual quality is iterated in V1.1+).
// What we DO assert is :
//   - The registry export exists and is keyed by achievement code.
//   - The 2 V1 codes (`r_365`, `sec_konami`) resolve to a function component.
//   - Unknown codes resolve to undefined (so the celebration modal can
//     fall back to the generic <AchievementCelebrationModal />).
import {
  BESPOKE_ANIMATIONS,
  hasBespoke,
} from '@/components/achievements/bespoke-animations';

describe('BESPOKE_ANIMATIONS registry', () => {
  it('exposes a component for r_365 (year-long streak)', () => {
    expect(typeof BESPOKE_ANIMATIONS['r_365']).toBe('function');
  });

  it('exposes a component for sec_konami', () => {
    expect(typeof BESPOKE_ANIMATIONS['sec_konami']).toBe('function');
  });

  it('returns undefined for unknown codes', () => {
    expect(BESPOKE_ANIMATIONS['v_first']).toBeUndefined();
    expect(BESPOKE_ANIMATIONS['nope']).toBeUndefined();
  });

  it('hasBespoke() is true for known codes only', () => {
    expect(hasBespoke('r_365')).toBe(true);
    expect(hasBespoke('sec_konami')).toBe(true);
    expect(hasBespoke('v_first')).toBe(false);
    expect(hasBespoke(null)).toBe(false);
    expect(hasBespoke(undefined)).toBe(false);
  });
});
