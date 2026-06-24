// ratis_client/hooks/use-feed-jack.ts
//
// Bug 7 (PO ticket 2026-05-12) — wire the "Feed Jack" CTA on the dashboard.
//
// Backend endpoint : `POST /api/v1/gamification/streak/feed` (cf
// `webservices/ratis_rewards/routes/gamification/streak.py`). The route
// extends the streak by 1, auto-consumes food reserves when days were
// missed, awards XP for the feed action, and returns the updated streak
// state. Idempotent — feeding twice in the same day just returns the
// current state.
//
// On success we invalidate :
//   - ['streak']      — JackStreakButton re-renders with fed=true
//   - ['cab-balance'] — header CAB pill (no debit but reserve cost may
//                       have applied, so keep it fresh)
//   - ['battlepass']  — XP credit from `xp_per_feed_jack` setting flows
//                       into the season XP gauge
//
// The server may return a 409 `needs_repair_required` if the streak has a
// 1-day gap and no reserves are available — callers should surface a
// "Repair streak" CTA in that case. V1 keeps the surface minimal : the
// mutation just exposes `isError` and the caller can show a toast.
//
// Body shape : `timezone` is optional (IANA string, sent on first call or
// when device tz changes). V1 omits it — the backend falls back to the
// stored streak row's tz_hint.

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type { StreakState } from '@/types/gamification';

export function useFeedJack() {
  const qc = useQueryClient();
  return useMutation<StreakState, Error, void>({
    mutationFn: () =>
      rewardsClient.post<StreakState>('/gamification/streak/feed', {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['streak'] });
      void qc.invalidateQueries({ queryKey: ['cab-balance'] });
      void qc.invalidateQueries({ queryKey: ['battlepass'] });
    },
  });
}
