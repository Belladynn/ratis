// ratis_client/components/achievements/unlock-overlay.tsx
//
// Achievements V1 — central UI overlay (PR 8/8 + V1.1 polish).
//
// Mounted ONCE at app root (cf `app/_layout.tsx`). Subscribes to the
// achievement bus and orchestrates the visual chain :
//
//                        ┌──────────────┐
//   bus dispatch ───────→│ FIFO queue   │──→ pull next ──→ render
//                        └──────────────┘
//                              │
//                              ▼
//                       1. AchievementUnlockToast (always)
//                       2. AchievementCelebrationModal (if show_modal &&
//                          rarity ≥ emerald && !bespoke)
//                          OR Bespoke component (if has_bespoke && registered)
//
// Toast and modal can co-exist on screen — the modal sits behind the toast
// (lower zIndex). The user dismisses the modal explicitly ; the toast self-
// dismisses after 4500ms or on tap.
//
// We intentionally do NOT block new toasts while one is visible — the queue
// drains FIFO ; if multiple unlocks fire in a single batch (e.g. nightly
// batch), the user sees them in sequence as each toast dismisses.
//
// V1.1 — toast queue cap (`MAX_TOAST_QUEUE = 10`) :
//   Each toast holds the screen for 4500 ms. A user that unlocks 150
//   trophées at once (e.g. nightly batch consolidation) would otherwise
//   suffer 11 min of stacked toasts → spam catastrophe. We cap at 10 visible
//   toasts and accumulate the surplus in a counter ; when the queue is
//   drained we enqueue ONE final "summary" toast :
//     "+N trophées débloqués 🏆"
//   The profil tab badge (cf `services/achievement-notification-handler.ts`)
//   already increments unconditionally for every dispatch, so the user can
//   review the full list inside the modal regardless of how many toasts
//   actually rendered.
//
// Pitfalls avoided :
//   - StrictMode double-subscribe : the cleanup in `useEffect` returns the
//     unsub thunk so React's effects model handles re-mount cleanly.
//   - Stale closure : `currentToast` / `currentModal` derive from state, not
//     refs, so the rendered payload always reflects the latest queue head.
//   - Summary recursion : the summary is enqueued only on a *real* dismiss
//     transition (queue had ≥1 item, now becoming 0). If a brand-new unlock
//     arrives between the summary and its dismissal it queues normally
//     behind it ; the summary itself does NOT count toward MAX_TOAST_QUEUE
//     (it's already a consolidation).

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Vibration } from 'react-native';

import { AchievementUnlockToast } from '@/components/achievements/unlock-toast';
import { AchievementCelebrationModal } from '@/components/achievements/celebration-modal';
import {
  BESPOKE_ANIMATIONS,
  hasBespoke,
} from '@/components/achievements/bespoke-animations';
import { achievementBus } from '@/services/achievement-notification-handler';
import type { AchievementUnlockedPayload } from '@/types/achievements';

/** Hard cap on visible toasts in the FIFO queue. Beyond this we drop and
 *  surface a single "+N trophées débloqués" summary toast at the end. */
export const MAX_TOAST_QUEUE = 10;

/** Sentinel `code` value carried by the summary payload — lets the rest of
 *  the pipeline (and tests) recognise the synthetic toast. */
export const SUMMARY_TOAST_CODE = '__achievements_summary__';

function buildSummaryPayload(droppedCount: number): AchievementUnlockedPayload {
  return {
    notif_type: 'achievement_unlocked',
    achievement_id: SUMMARY_TOAST_CODE,
    code: SUMMARY_TOAST_CODE,
    label: `+${droppedCount} trophées débloqués 🏆`,
    description: 'Ouvre l’écran Succès pour les voir tous',
    // Gold rarity = warm metallic frame, no holo sweep, no modal trigger.
    // Visually neutral but unmistakably a "trophy" event.
    rarity: 'gold',
    category: 'volume',
    icon: '🏆',
    cab_granted: 0,
    show_modal: false,
    has_bespoke: false,
    // Soft haptic only — the user already felt one per individual unlock.
    sound_intensity: 1,
  };
}

const MODAL_RARITIES = new Set([
  'emerald',
  'sapphire',
  'ruby',
  'crystal',
  'diamond',
] as const);

/**
 * Map sound_intensity 0-3 to a Vibration pattern (ms).
 *   0 → no haptic
 *   1 → short pulse
 *   2 → medium pulse
 *   3 → strong pattern
 *
 * We use react-native's `Vibration` (always available, no extra dep) instead
 * of `expo-haptics` to keep this PR's footprint minimal. The nuance lost is
 * the "selection / impact / notification" semantics — a follow-up V1.1 will
 * swap to Haptics once that dep lands.
 */
function triggerHaptic(intensity: number): void {
  if (intensity <= 0) return;
  if (intensity === 1) Vibration.vibrate(60);
  else if (intensity === 2) Vibration.vibrate(120);
  else Vibration.vibrate([0, 80, 60, 120]);
}

export function AchievementUnlockOverlay() {
  // Two queues so a sapphire unlock keeps its toast on screen WHILE its
  // celebration modal is also up (per spec — they overlap, the modal sits
  // behind, the toast above).
  const [toastQueue, setToastQueue] = useState<AchievementUnlockedPayload[]>(
    [],
  );
  const [modalQueue, setModalQueue] = useState<AchievementUnlockedPayload[]>(
    [],
  );

  // V1.1 — count of payloads dropped because the toast queue was full.
  // Held in a ref (no re-render needed on increment) ; we read it inside
  // `dismissToast` to decide whether to emit the summary toast.
  const droppedCountRef = useRef(0);

  useEffect(() => {
    const unsubscribe = achievementBus.subscribe((payload) => {
      triggerHaptic(payload.sound_intensity);
      setToastQueue((q) => {
        if (q.length >= MAX_TOAST_QUEUE) {
          // Drop the surplus ; the badge counter (incremented unconditionally
          // by `dispatchAchievementUnlocked`) keeps an accurate total for
          // the modal screen. The summary toast at drain time will surface
          // the dropped count to the user.
          droppedCountRef.current += 1;
          return q;
        }
        return [...q, payload];
      });
      // The modal queue only receives "celebration-worthy" payloads — others
      // get the toast only. Bespoke takes precedence over generic modal.
      // We deliberately do NOT cap the modal queue : modals are user-driven
      // dismissals (much rarer + much higher signal value than toasts).
      const isModalRarity =
        (MODAL_RARITIES as ReadonlySet<string>).has(payload.rarity);
      if (payload.show_modal && (isModalRarity || hasBespoke(payload.code))) {
        setModalQueue((q) => [...q, payload]);
      }
    });
    return unsubscribe;
  }, []);

  const currentToast = toastQueue[0] ?? null;
  const currentModal = modalQueue[0] ?? null;

  const dismissToast = useCallback(() => {
    setToastQueue((q) => {
      const remaining = q.slice(1);
      // If draining the queue to empty AND we dropped at least one payload
      // since the last drain, emit a single summary toast and reset the
      // counter. The summary itself is a regular FIFO entry — if a brand-new
      // unlock arrives during its 4500ms it queues behind it normally.
      if (remaining.length === 0 && droppedCountRef.current > 0) {
        const dropped = droppedCountRef.current;
        droppedCountRef.current = 0;
        return [buildSummaryPayload(dropped)];
      }
      return remaining;
    });
  }, []);

  const dismissModal = useCallback(() => {
    setModalQueue((q) => q.slice(1));
  }, []);

  // Resolve the modal layer : bespoke wins ; otherwise generic modal ; else
  // nothing (terracotta / bronze / copper / silver / gold get toast only).
  let modalLayer: React.ReactNode = null;
  if (currentModal) {
    const Bespoke =
      currentModal.has_bespoke && currentModal.code
        ? BESPOKE_ANIMATIONS[currentModal.code]
        : undefined;
    if (Bespoke) {
      modalLayer = (
        <Bespoke payload={currentModal} onDismiss={dismissModal} />
      );
    } else if ((MODAL_RARITIES as ReadonlySet<string>).has(currentModal.rarity)) {
      modalLayer = (
        <AchievementCelebrationModal
          payload={currentModal}
          onDismiss={dismissModal}
        />
      );
    } else {
      // Should not happen given the gate above — drop it from the queue
      // defensively to avoid wedging.
      dismissModal();
    }
  }

  return (
    <>
      {modalLayer}
      <AchievementUnlockToast payload={currentToast} onDismiss={dismissToast} />
    </>
  );
}

export default AchievementUnlockOverlay;
