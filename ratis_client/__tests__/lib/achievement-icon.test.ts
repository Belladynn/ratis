// ratis_client/__tests__/lib/achievement-icon.test.ts
//
// Bug 1 (PO ticket 2026-05-12 wave 3) — per-achievement icon mapping.
//
// Parametrized over the 23 seeded achievements (mirror of
// `ratis_core/seed_achievements.py`, minus `sea_winter` which is
// window-closed). Each row asserts the helper returns the PO-approved
// emoji for the code.

import {
  getAchievementIcon,
  KNOWN_ACHIEVEMENT_CODES,
} from '@/lib/achievement-icon';

type SeededIcon = {
  code: string;
  expectedIcon: string;
  iconCode: string; // backend short code from `seed_achievements.py`
};

// The 23 V1 achievements with their PO-approved emoji + backend icon
// code. The expected emoji comes from the handoff JSX
// (`Ratis_handoff/lib/ratis-achievements-data.jsx`) for codes covered
// there, or our proposed picks (documented in the PR body) otherwise.
const SEEDED: readonly SeededIcon[] = [
  // ── VOLUME ──
  { code: 'v_first', iconCode: 'x', expectedIcon: '🎬' },
  { code: 'v_10', iconCode: 'list', expectedIcon: '📋' },
  { code: 'v_50', iconCode: 'list2', expectedIcon: '📑' },
  { code: 'v_500', iconCode: 'chart', expectedIcon: '📊' },
  { code: 'v_1000', iconCode: 'trophy', expectedIcon: '🏆' },
  // ── SAVINGS ──
  { code: 's_1', iconCode: 'coin', expectedIcon: '🪙' },
  { code: 's_10', iconCode: 'bill1', expectedIcon: '💵' },
  { code: 's_50', iconCode: 'bill2', expectedIcon: '💴' },
  { code: 's_500', iconCode: 'bill3', expectedIcon: '💷' },
  { code: 's_day_20', iconCode: 'star', expectedIcon: '🌟' },
  // ── STREAK ──
  { code: 'r_3', iconCode: 'fire', expectedIcon: '🔥' },
  { code: 'r_7', iconCode: 'fire', expectedIcon: '🔥' },
  { code: 'r_14', iconCode: 'fire', expectedIcon: '🔥' },
  { code: 'r_30', iconCode: 'fire', expectedIcon: '🔥' },
  { code: 'r_365', iconCode: 'milky', expectedIcon: '🌌' },
  // ── SOCIAL ──
  { code: 'soc_invite_1', iconCode: 'hands', expectedIcon: '🤝' },
  { code: 'soc_invite_10', iconCode: 'globe', expectedIcon: '🌐' },
  // ── EXPLORATION ──
  { code: 'exp_brand_5', iconCode: 'cart', expectedIcon: '🛒' },
  { code: 'exp_cat_15', iconCode: 'books', expectedIcon: '📚' },
  { code: 'exp_unknown_10', iconCode: 'rocket', expectedIcon: '🚀' },
  // ── SEASONAL ──
  { code: 'sea_summer', iconCode: 'sun', expectedIcon: '☀️' },
  // ── SECRET ──
  { code: 'sec_konami', iconCode: 'qmark', expectedIcon: '❓' },
  { code: 'sec_3am', iconCode: 'qmark', expectedIcon: '❓' },
];

describe('getAchievementIcon — seeded achievements (PO challenge target)', () => {
  it('covers the 23 V1 seeded achievements (sea_winter omis car window closed)', () => {
    expect(SEEDED).toHaveLength(23);
  });

  it.each(SEEDED.map((s) => [s.code, s.expectedIcon] as const))(
    'achievement %s resolves to %s',
    (code, expected) => {
      expect(getAchievementIcon({ code })).toBe(expected);
    },
  );

  it('exposes every seeded code in KNOWN_ACHIEVEMENT_CODES', () => {
    for (const { code } of SEEDED) {
      expect(KNOWN_ACHIEVEMENT_CODES).toContain(code);
    }
    // Sanity : the helper knows about exactly 23 codes (sea_winter omis).
    expect(KNOWN_ACHIEVEMENT_CODES).toHaveLength(23);
  });
});

describe('getAchievementIcon — fallback paths', () => {
  it('falls back to the backend icon-code mapping when code is unknown', () => {
    // `v_barcode_first` is not in our V1 explicit map ; its backend icon
    // code from seed_achievements would be `"x"` which the fallback knows.
    expect(
      getAchievementIcon({ code: 'unknown_future_code', iconCode: 'fire' }),
    ).toBe('🔥');
  });

  it('prefers the explicit code over the fallback when both are provided', () => {
    // v_first (explicit = 🎬) wins over a deliberately wrong icon code.
    expect(
      getAchievementIcon({ code: 'v_first', iconCode: 'fire' }),
    ).toBe('🎬');
  });

  it('returns ❓ when neither code nor iconCode resolves', () => {
    expect(getAchievementIcon({})).toBe('❓');
    expect(
      getAchievementIcon({ code: 'unknown', iconCode: 'unknown' }),
    ).toBe('❓');
  });

  it('handles null/undefined inputs defensively', () => {
    expect(getAchievementIcon({ code: null, iconCode: null })).toBe('❓');
    expect(getAchievementIcon({ code: undefined })).toBe('❓');
  });
});
