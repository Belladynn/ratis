// ratis_client/hooks/use-konami-code.ts
//
// Achievements V1 — Konami code detector for mobile (PR 8/8).
//
// Mobile devices don't have a physical keyboard ; we expose a `push(key)`
// API that the caller wires from gesture handlers (swipe up/down/left/right
// = arrows, tap zones = A/B). The hook tracks the sequence index and fires
// `onComplete` once the canonical 10-step sequence (↑↑↓↓←→←→BA) is
// reproduced, with greedy restart so a wrong key that happens to be the
// first symbol ('up') keeps the streak alive at index 1.
//
// V1 scope : ship the hook + its contract. Wiring it to actual app
// gestures (a hidden swipe-listener overlay or a debug menu) is a V1.1
// follow-up — see SESSION_LOG entry on this PR. For V1 the integration
// in `app/_layout.tsx` calls `triggerSecretEvent('konami_code_entered')`
// when `onComplete` fires, so the moment the wiring lands the rest works
// out of the box.
//
// Why a `push(key)` API rather than wrapping the whole app in an event
// listener ?
//   - RN doesn't ship a global gesture broker the way the web's
//     `document.addEventListener('keydown', …)` does. Each owner of a
//     gesture must opt-in.
//   - Tests can drive the hook deterministically without faking gestures.
//   - The hook is reusable for future "secret combo" features (e.g. a
//     5-tap unlock on the logo).

import { useCallback, useRef } from 'react';

export type KonamiKey =
  | 'up'
  | 'down'
  | 'left'
  | 'right'
  | 'a'
  | 'b';

export const KONAMI_SEQUENCE: readonly KonamiKey[] = [
  'up',
  'up',
  'down',
  'down',
  'left',
  'right',
  'left',
  'right',
  'b',
  'a',
];

export interface UseKonamiCodeApi {
  /** Push the next gesture symbol. Use one of the KonamiKey values. */
  push: (key: KonamiKey) => void;
  /** Forcibly reset the progress to 0. */
  reset: () => void;
}

/**
 * Greedy index advance — return the next index given the current index and
 * the just-pushed key. If the key matches the expected next symbol → +1.
 * If it doesn't match but happens to match step 0 → restart at 1 (so
 * "up,a,up,up,down,…" still progresses through the sequence). Otherwise
 * → 0.
 */
function advance(currentIndex: number, key: KonamiKey): number {
  if (key === KONAMI_SEQUENCE[currentIndex]) return currentIndex + 1;
  if (key === KONAMI_SEQUENCE[0]) return 1;
  return 0;
}

export function useKonamiCode(onComplete: () => void): UseKonamiCodeApi {
  const indexRef = useRef(0);

  const push = useCallback(
    (key: KonamiKey) => {
      const next = advance(indexRef.current, key);
      indexRef.current = next;
      if (next === KONAMI_SEQUENCE.length) {
        indexRef.current = 0;
        try {
          onComplete();
        } catch {
          // Subscriber failure must not poison the hook state.
        }
      }
    },
    [onComplete],
  );

  const reset = useCallback(() => {
    indexRef.current = 0;
  }, []);

  return { push, reset };
}
