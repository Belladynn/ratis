// ratis_client/__tests__/components/profil/achievements-adapter.test.ts
//
// Achievements V1 — live API → legacy card shape adapter (PR 8/8).
import {
  flattenAchievementsList,
  toLegacyAchievement,
} from '@/components/profil/achievements-adapter';
import type {
  AchievementCategoryGroup,
  AchievementItem,
  AchievementsListResponse,
} from '@/types/achievements';

const ITEM_UNLOCKED: AchievementItem = {
  id: 'aaaa',
  code: 'v_first',
  label: 'Premier scan',
  description: 'Scanner ton tout premier ticket',
  icon: '🎬',
  rarity: 'terracotta',
  category: 'volume',
  cab_reward: 20,
  target_value: 1,
  progress: null,
  unlocked: true,
  unlocked_at: '2026-04-15T08:30:00+00:00',
  window_open: true,
};

const ITEM_LOCKED: AchievementItem = {
  ...ITEM_UNLOCKED,
  id: 'bbbb',
  code: 'r_30',
  label: 'Mois sans rater',
  rarity: 'sapphire',
  category: 'streak',
  cab_reward: 250,
  target_value: 30,
  unlocked: false,
  unlocked_at: null,
};

const ITEM_SECRET_MASKED: AchievementItem = {
  id: 'cccc',
  code: null,
  label: '???',
  description: 'Mystère...',
  icon: '❓',
  rarity: 'diamond',
  category: 'secret',
  cab_reward: null,
  target_value: null,
  progress: null,
  unlocked: false,
  unlocked_at: null,
  window_open: true,
};

describe('toLegacyAchievement', () => {
  it('maps unlocked item → status="unlocked" with progress=target=1', () => {
    const legacy = toLegacyAchievement(ITEM_UNLOCKED);
    expect(legacy.status).toBe('unlocked');
    expect(legacy.target).toBe(1);
    expect(legacy.progress).toBe(1);
    expect(legacy.label).toBe('Premier scan');
    expect(legacy.icon).toBe('🎬');
  });

  it('maps locked item → status="locked" with progress=0 target=target_value', () => {
    const legacy = toLegacyAchievement(ITEM_LOCKED);
    expect(legacy.status).toBe('locked');
    expect(legacy.target).toBe(30);
    expect(legacy.progress).toBe(0);
  });

  it('maps secret-masked item → status="locked" target=1 (defensive defaults)', () => {
    const legacy = toLegacyAchievement(ITEM_SECRET_MASKED);
    expect(legacy.status).toBe('locked');
    expect(legacy.target).toBe(1);
    expect(legacy.progress).toBe(0);
    expect(legacy.icon).toBe('❓');
  });

  // Bug 1 (PO ticket 2026-05-12 wave 3) — the backend ships `icon` as a
  // short code (e.g. "fire"), not an emoji. The adapter resolves the
  // emoji via the central helper using the row's `code`.
  it('resolves the icon emoji from the achievement code (Bug 1 wave 3)', () => {
    // Backend payload : `icon` = "fire" (short code), `code` = "r_30"
    const item: AchievementItem = {
      ...ITEM_LOCKED,
      icon: 'fire',
    };
    const legacy = toLegacyAchievement(item);
    expect(legacy.icon).toBe('🔥');
  });

  it('resolves the icon from the icon-code fallback when code is unknown', () => {
    const item: AchievementItem = {
      ...ITEM_UNLOCKED,
      code: 'unknown_future_code',
      icon: 'trophy',
    };
    const legacy = toLegacyAchievement(item);
    expect(legacy.icon).toBe('🏆');
  });

  it('falls back to "secret" category if input category is j_y_etais (legacy types do not know j_y_etais)', () => {
    const legacy = toLegacyAchievement({
      ...ITEM_UNLOCKED,
      category: 'j_y_etais',
    });
    // We map j_y_etais → seasonal in the legacy shape (only 7 categories
    // available pre-V1). The bucket-level grouping handled separately.
    expect(legacy.category).toBe('seasonal');
  });
});

describe('flattenAchievementsList', () => {
  const RESPONSE: AchievementsListResponse = {
    categories: [
      {
        key: 'volume',
        label: 'Scans',
        items: [ITEM_UNLOCKED],
      } as AchievementCategoryGroup,
      {
        key: 'streak',
        label: 'Régularité',
        items: [ITEM_LOCKED],
      } as AchievementCategoryGroup,
    ],
  };

  it('flattens categories into a single Achievement[] preserving order', () => {
    const out = flattenAchievementsList(RESPONSE);
    expect(out).toHaveLength(2);
    expect(out[0].id).toBe('aaaa');
    expect(out[1].id).toBe('bbbb');
  });

  it('returns empty array for empty response', () => {
    const out = flattenAchievementsList({ categories: [] });
    expect(out).toEqual([]);
  });

  it('handles undefined response defensively', () => {
    expect(flattenAchievementsList(undefined)).toEqual([]);
    expect(flattenAchievementsList(null)).toEqual([]);
  });
});
