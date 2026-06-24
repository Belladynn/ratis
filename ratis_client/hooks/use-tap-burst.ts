// ratis_client/hooks/use-tap-burst.ts
//
// Achievements V1.1 — generic "tap N times within Tms" detector.
//
// Decoupled from any specific UI element : the caller wires `register()` to
// whatever gesture they want (a `Pressable.onPress`, a `LongPress.onPress`,
// etc.) and we count consecutive registrations within a sliding window. Once
// the threshold is met, `onComplete` fires and the counter resets.
//
// Design choices :
//   - Generic on purpose. The current consumer is the V1.1 Konami-substitute
//     (5 taps on the profil avatar within 1.5s) but the hook will outlive
//     that integration — easy pivot to "tap 3× on the dashboard CAB pill",
//     "tap 7× on the level badge", etc.
//   - No re-render on tap : we keep the counter in a `useRef` so visual
//     siblings don't repaint on every increment. The only state-changing
//     side-effect is the `onComplete` callback which the caller controls.
//   - Discrete "session" reset : when the inactivity window expires the
//     counter snaps back to 0. New tap starts a fresh burst at 1.
//   - StrictMode double-invoke is benign : `register()` is a stable callback
//     and the timeout is cleared on unmount.
//
// Why not reuse `useKonamiCode` ? That hook tracks an *ordered, multi-symbol*
// sequence (↑↑↓↓←→←→BA). The V1.1 ship is a *single-symbol burst*. Different
// shape, kept separate ; both coexist for V2 (swipe pattern wiring planned).
//
// Pitfalls avoided :
//   - Stale closure on `onComplete` : `useCallback` re-creates `register`
//     when the callback identity changes, so each burst always fires the
//     latest handler.
//   - Timer leak on unmount : cleanup effect cancels any pending reset
//     timer.

import { useCallback, useEffect, useRef } from 'react';

export interface UseTapBurstOptions {
  /** How many taps must accumulate within the window. */
  threshold: number;
  /** Milliseconds of inactivity that resets the burst back to 0. */
  windowMs: number;
  /** Fired exactly once each time the threshold is reached. */
  onComplete: () => void;
}

export interface UseTapBurstApi {
  /** Call from your gesture handler (e.g. `<Pressable onPress={register} />`). */
  register: () => void;
  /** Force the burst counter to 0 (cancels any pending reset). */
  reset: () => void;
}

export function useTapBurst({
  threshold,
  windowMs,
  onComplete,
}: UseTapBurstOptions): UseTapBurstApi {
  const countRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    countRef.current = 0;
    clearTimer();
  }, [clearTimer]);

  const register = useCallback(() => {
    // Defensive : a non-positive threshold disables the hook entirely.
    if (threshold <= 0) return;

    clearTimer();
    countRef.current += 1;

    if (countRef.current >= threshold) {
      countRef.current = 0;
      try {
        onComplete();
      } catch {
        // Subscriber failure must not poison the hook state — caller is
        // responsible for its own error reporting (Sentry, toast, etc.).
      }
      return;
    }

    // Schedule the inactivity reset. Replaced on next tap above.
    timerRef.current = setTimeout(() => {
      countRef.current = 0;
      timerRef.current = null;
    }, windowMs);
  }, [threshold, windowMs, onComplete, clearTimer]);

  // Cleanup any pending reset on unmount.
  useEffect(() => clearTimer, [clearTimer]);

  return { register, reset };
}
