import {
  computeRings,
  getFossilOpacity,
  DEFAULT_SUBSCRIPTION_PRICE_CENTS,
} from '@/utils/roi-rings';

describe('computeRings', () => {
  it('returns 0 rings and 0 fill when savings = 0', () => {
    const r = computeRings(0);
    expect(r.completedRings).toBe(0);
    expect(r.currentFill).toBe(0);
    expect(r.totalAbonnements).toBe(0);
    expect(r.prestigeLevel).toBe(0);
  });

  it('returns correct fill for partial first ring (3.99€ = 49.9%)', () => {
    const r = computeRings(399); // 3.99€ en centimes
    expect(r.completedRings).toBe(0);
    expect(r.currentFill).toBeCloseTo(0.499, 2);
    expect(r.prestigeLevel).toBe(0);
  });

  it('counts completed rings correctly (15.98€ = 2 rings + fill)', () => {
    const r = computeRings(1598); // 15.98€
    expect(r.completedRings).toBe(2);
    expect(r.currentFill).toBeCloseTo(0, 1);
    expect(r.totalAbonnements).toBeCloseTo(2.0, 1);
  });

  it('detects prestige at 10 completed rings (79.90€)', () => {
    const r = computeRings(7990); // 79.90€
    expect(r.completedRings).toBe(10);
    expect(r.prestigeLevel).toBe(1);
  });

  it('prestige 2 at 20 rings (159.80€)', () => {
    const r = computeRings(15980);
    expect(r.prestigeLevel).toBe(2);
  });

  it('capped fossil display at 10', () => {
    const r = computeRings(15980); // 20 rings
    expect(r.displayFossils).toBe(10);
  });
});

describe('getFossilOpacity', () => {
  it('most recent fossil (index 0) has highest opacity 0.7', () => {
    expect(getFossilOpacity(0, 5)).toBeCloseTo(0.7);
  });

  it('oldest fossil has lowest opacity ~0.3', () => {
    expect(getFossilOpacity(4, 5)).toBeCloseTo(0.3);
  });
});

describe('DEFAULT_SUBSCRIPTION_PRICE_CENTS', () => {
  it('is 799 (7.99€) — fallback before backend responds', () => {
    expect(DEFAULT_SUBSCRIPTION_PRICE_CENTS).toBe(799);
  });
});

describe('computeRings — parameterised subscription price', () => {
  it('respects a custom subscription price from the backend', () => {
    // 1000c savings / 500c price = 2 rings exactly.
    const r = computeRings(1000, 500);
    expect(r.completedRings).toBe(2);
    expect(r.currentFill).toBe(0);
    expect(r.totalAbonnements).toBe(2);
  });

  it('falls back to default when subscriptionPriceCents <= 0', () => {
    const r = computeRings(1598, 0);
    // 1598 / 799 = 2.0
    expect(r.completedRings).toBe(2);
  });
});

import { RING_COLORS, getRingColor } from '@/utils/roi-rings';

describe('RING_COLORS', () => {
  it('exposes 10 distinct colors (one per ring before prestige)', () => {
    expect(RING_COLORS).toHaveLength(10);
    const unique = new Set(RING_COLORS);
    expect(unique.size).toBe(10);
  });

  it('starts with cyan and ends with violet (cool → warm progression)', () => {
    expect(RING_COLORS[0]).toBe('#22D3EE');
    expect(RING_COLORS[9]).toBe('#A855F7');
  });
});

describe('getRingColor', () => {
  it('returns the color for ring 1 (index 0)', () => {
    expect(getRingColor(0)).toBe('#22D3EE');
  });

  it('returns the color for ring 10 (index 9)', () => {
    expect(getRingColor(9)).toBe('#A855F7');
  });

  it('cycles after prestige (index 10 returns color of ring 1)', () => {
    expect(getRingColor(10)).toBe('#22D3EE');
    expect(getRingColor(20)).toBe('#22D3EE');
  });

  it('handles negative index gracefully (returns first color)', () => {
    expect(getRingColor(-1)).toBe('#22D3EE');
  });
});
