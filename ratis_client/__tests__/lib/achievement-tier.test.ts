// ratis_client/__tests__/lib/achievement-tier.test.ts
//
// Bug 4 (PO ticket 2026-05-12 wave 2) — 5-tier consolidation for the
// achievements catalogue. Verifies the rarity → tier mapping for every
// rarity AND for each of the 23 seeded achievement codes (so the PO can
// challenge specific assignments without re-reading the helper code).

import {
  getAchievementTier,
  getAchievementTierColor,
  getAchievementTierLabel,
  TIER_COLORS,
  TIER_LABELS,
} from '@/lib/achievement-tier';
import type { RarityKey } from '@/components/profil/achievements-data';

type SeededAchievement = {
  code: string;
  rarity: RarityKey;
  expectedTier:
    | 'terre_cuite'
    | 'bronze'
    | 'cuivre'
    | 'argent'
    | 'or';
};

// The 23 seeded achievements (mirror of `ratis_core/seed_achievements.py`,
// minus `sea_winter` which is window-closed). The expected tier is
// derived from the rarity assignment in the seed.
const SEEDED: readonly SeededAchievement[] = [
  // ── VOLUME ──
  { code: 'v_first', rarity: 'terracotta', expectedTier: 'terre_cuite' },
  { code: 'v_10', rarity: 'bronze', expectedTier: 'bronze' },
  { code: 'v_50', rarity: 'copper', expectedTier: 'cuivre' },
  { code: 'v_500', rarity: 'gold', expectedTier: 'or' },
  { code: 'v_1000', rarity: 'crystal', expectedTier: 'or' },
  // ── SAVINGS ──
  { code: 's_1', rarity: 'terracotta', expectedTier: 'terre_cuite' },
  { code: 's_10', rarity: 'bronze', expectedTier: 'bronze' },
  { code: 's_50', rarity: 'copper', expectedTier: 'cuivre' },
  { code: 's_500', rarity: 'sapphire', expectedTier: 'or' },
  { code: 's_day_20', rarity: 'emerald', expectedTier: 'or' },
  // ── STREAK ──
  { code: 'r_3', rarity: 'bronze', expectedTier: 'bronze' },
  { code: 'r_7', rarity: 'copper', expectedTier: 'cuivre' },
  { code: 'r_14', rarity: 'silver', expectedTier: 'argent' },
  { code: 'r_30', rarity: 'sapphire', expectedTier: 'or' },
  { code: 'r_365', rarity: 'diamond', expectedTier: 'or' },
  // ── SOCIAL ──
  { code: 'soc_invite_1', rarity: 'bronze', expectedTier: 'bronze' },
  { code: 'soc_invite_10', rarity: 'gold', expectedTier: 'or' },
  // ── EXPLORATION ──
  { code: 'exp_brand_5', rarity: 'bronze', expectedTier: 'bronze' },
  { code: 'exp_cat_15', rarity: 'gold', expectedTier: 'or' },
  { code: 'exp_unknown_10', rarity: 'emerald', expectedTier: 'or' },
  // ── SEASONAL ──
  { code: 'sea_summer', rarity: 'gold', expectedTier: 'or' },
  // ── SECRET ──
  { code: 'sec_konami', rarity: 'diamond', expectedTier: 'or' },
  { code: 'sec_3am', rarity: 'gold', expectedTier: 'or' },
];

describe('getAchievementTier — rarity → tier consolidation', () => {
  it.each([
    ['terracotta', 'terre_cuite'],
    ['bronze', 'bronze'],
    ['copper', 'cuivre'],
    ['silver', 'argent'],
    ['gold', 'or'],
    ['emerald', 'or'],
    ['sapphire', 'or'],
    ['ruby', 'or'],
    ['crystal', 'or'],
    ['diamond', 'or'],
  ] as const)('rarity %s maps to tier %s', (rarity, expected) => {
    expect(getAchievementTier({ code: 'arbitrary', rarity })).toBe(expected);
  });
});

describe('getAchievementTier — seeded achievements (PO challenge target)', () => {
  it.each(SEEDED.map((s) => [s.code, s.rarity, s.expectedTier] as const))(
    'achievement %s (rarity %s) → tier %s',
    (code, rarity, expected) => {
      expect(getAchievementTier({ code, rarity })).toBe(expected);
    },
  );

  it('covers all 23 seeded achievements', () => {
    expect(SEEDED).toHaveLength(23);
  });
});

describe('getAchievementTierColor / getAchievementTierLabel', () => {
  it('returns the palette colour matching the tier', () => {
    expect(
      getAchievementTierColor({ code: 'v_first', rarity: 'terracotta' }),
    ).toBe(TIER_COLORS.terre_cuite);
    expect(
      getAchievementTierColor({ code: 'r_365', rarity: 'diamond' }),
    ).toBe(TIER_COLORS.or);
  });

  it('returns the FR label matching the tier', () => {
    expect(
      getAchievementTierLabel({ code: 'r_14', rarity: 'silver' }),
    ).toBe(TIER_LABELS.argent);
    expect(
      getAchievementTierLabel({ code: 's_50', rarity: 'copper' }),
    ).toBe(TIER_LABELS.cuivre);
  });
});

describe('palette definitions', () => {
  it('exposes a label + colour for each of the 5 tiers', () => {
    const tiers = ['terre_cuite', 'bronze', 'cuivre', 'argent', 'or'] as const;
    for (const t of tiers) {
      expect(TIER_LABELS[t]).toBeTruthy();
      expect(TIER_COLORS[t]).toMatch(/^#[0-9A-F]{6}$/i);
    }
  });
});
