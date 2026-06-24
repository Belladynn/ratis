// ratis_client/__tests__/services/achievement-notification-handler.test.ts
//
// Achievements V1 — notification handler queue + tab badge tests (PR 8/8).
import {
  achievementBus,
  dispatchAchievementUnlocked,
  getProfileTabBadge,
  incrementProfileTabBadge,
  resetProfileTabBadge,
  PROFILE_TAB_BADGE_KEY,
} from '@/services/achievement-notification-handler';
import type { AchievementUnlockedPayload } from '@/types/achievements';
import AsyncStorage from '@react-native-async-storage/async-storage';

const BASE: AchievementUnlockedPayload = {
  notif_type: 'achievement_unlocked',
  achievement_id: 'aaaa-1111',
  code: 'v_first',
  label: 'Premier scan',
  description: 'Scanner ton tout premier ticket',
  rarity: 'terracotta',
  category: 'volume',
  icon: '🎬',
  cab_granted: 20,
  show_modal: false,
  has_bespoke: false,
  sound_intensity: 1,
};

describe('achievementBus', () => {
  beforeEach(() => {
    achievementBus.clear();
  });

  it('notifies subscribers when an unlock is dispatched', () => {
    const listener = jest.fn();
    const unsub = achievementBus.subscribe(listener);
    dispatchAchievementUnlocked(BASE);
    expect(listener).toHaveBeenCalledWith(BASE);
    unsub();
  });

  it('does not notify after unsubscribe', () => {
    const listener = jest.fn();
    const unsub = achievementBus.subscribe(listener);
    unsub();
    dispatchAchievementUnlocked(BASE);
    expect(listener).not.toHaveBeenCalled();
  });

  it('supports multiple concurrent subscribers', () => {
    const a = jest.fn();
    const b = jest.fn();
    achievementBus.subscribe(a);
    achievementBus.subscribe(b);
    dispatchAchievementUnlocked(BASE);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });
});

describe('profile tab badge counter', () => {
  beforeEach(async () => {
    await AsyncStorage.removeItem(PROFILE_TAB_BADGE_KEY);
  });

  it('starts at 0', async () => {
    expect(await getProfileTabBadge()).toBe(0);
  });

  it('increments persistently', async () => {
    await incrementProfileTabBadge();
    await incrementProfileTabBadge();
    expect(await getProfileTabBadge()).toBe(2);
  });

  it('caps display at 99 in storage but returns the real count', async () => {
    // We don't artificially cap — display layer formats `99+`. But verify
    // raw count survives 99 increments.
    for (let i = 0; i < 100; i++) {
      await incrementProfileTabBadge();
    }
    expect(await getProfileTabBadge()).toBe(100);
  });

  it('resets back to 0', async () => {
    await incrementProfileTabBadge();
    await incrementProfileTabBadge();
    await resetProfileTabBadge();
    expect(await getProfileTabBadge()).toBe(0);
  });

  it('dispatchAchievementUnlocked auto-increments the badge', async () => {
    await dispatchAchievementUnlocked(BASE);
    expect(await getProfileTabBadge()).toBe(1);
  });
});
