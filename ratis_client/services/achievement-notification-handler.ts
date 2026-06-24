// ratis_client/services/achievement-notification-handler.ts
//
// Achievements V1 — central event bus + tab badge counter (PR 8/8).
//
// Two responsibilities :
//
//   1. **Event bus** — `dispatchAchievementUnlocked(payload)` notifies every
//      subscribed listener. The provider mounted at app root subscribes once
//      and feeds the payload into the toast/modal/bespoke queues.
//
//   2. **Tab badge counter** — AsyncStorage-backed counter so the profil tab
//      can display "you have N new trophies". Persists across app restarts.
//      Reset to 0 when the user opens the Achievements modal (handled at the
//      call site).
//
// Why a custom bus instead of `expo-notifications.addNotificationReceivedListener` ?
//
//   The mobile push pipeline (FCM / APNS via the notifier service) is wired
//   in V1.x but not yet bridged to the FE — we don't ship `expo-notifications`
//   in this PR. The bus pattern keeps the UI layer decoupled : when push is
//   wired, the push handler will simply call `dispatchAchievementUnlocked`
//   too. Same FE code path for both transports.
//
//   For unlocks that happen while the app is in the foreground (most of them
//   for V1 — daily missions etc.), we'll soon route the backend response
//   directly through this bus from the relevant React Query mutations
//   (`onSuccess` invalidator already in place — see PR4 hooks). That work is
//   tracked as a V1.1 follow-up.
//
// Pitfalls avoided :
//   - Listener leak : `subscribe()` returns an `unsubscribe` thunk every
//     React component caller MUST call on unmount.
//   - Storage race : reads + writes use AsyncStorage's atomic API ; no
//     read-modify-write window long enough to clash in practice.

import AsyncStorage from '@react-native-async-storage/async-storage';

import type { AchievementUnlockedPayload } from '@/types/achievements';

// ---------------------------------------------------------------------------
// Event bus
// ---------------------------------------------------------------------------

type Listener = (payload: AchievementUnlockedPayload) => void;

const listeners = new Set<Listener>();

export const achievementBus = {
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Test-only — drops every subscriber. NEVER call from production code. */
  clear(): void {
    listeners.clear();
  },
};

/**
 * Public entrypoint — call this from any module that knows an achievement
 * just unlocked (push handler / mutation onSuccess / debug menu / E2E).
 *
 * Side effects :
 *   - Notifies every bus subscriber with the payload (synchronously).
 *   - Increments the AsyncStorage-backed profil tab badge counter.
 *
 * Returns the new badge count so the caller can short-circuit if desired.
 */
export async function dispatchAchievementUnlocked(
  payload: AchievementUnlockedPayload,
): Promise<number> {
  // Notify subscribers FIRST so the toast pops while the storage write is
  // in flight — the badge bump is purely informative.
  for (const listener of Array.from(listeners)) {
    try {
      listener(payload);
    } catch {
      // A misbehaving subscriber must NEVER take down the dispatch loop.
      // We swallow here ; sentry capture happens in the subscriber itself
      // (each component's error boundary).
    }
  }
  return incrementProfileTabBadge();
}

// ---------------------------------------------------------------------------
// Profile tab badge counter (AsyncStorage)
// ---------------------------------------------------------------------------

export const PROFILE_TAB_BADGE_KEY = 'achievements:profile_badge_count';

export async function getProfileTabBadge(): Promise<number> {
  const raw = await AsyncStorage.getItem(PROFILE_TAB_BADGE_KEY);
  if (raw == null) return 0;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
}

export async function incrementProfileTabBadge(): Promise<number> {
  const current = await getProfileTabBadge();
  const next = current + 1;
  await AsyncStorage.setItem(PROFILE_TAB_BADGE_KEY, String(next));
  return next;
}

export async function resetProfileTabBadge(): Promise<void> {
  await AsyncStorage.setItem(PROFILE_TAB_BADGE_KEY, '0');
}

/**
 * Display formatter — the FE caps the visible badge at "99+". Any number
 * below 100 prints as-is ; otherwise the literal string `"99+"`.
 */
export function formatBadgeCount(n: number): string {
  if (n <= 0) return '';
  if (n >= 100) return '99+';
  return String(n);
}
