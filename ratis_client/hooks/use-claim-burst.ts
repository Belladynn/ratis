// ratis_client/hooks/use-claim-burst.ts
//
// Buffer + Burst (refonte 2026-05-09) — claim Burst paliers (XP only, 0 CAB).
//
// First claim flips `burst_locked = true` permanently → no more Buffer on
// this mission.
//
// On success we invalidate :
//   - ['missions']           — burst_count + burst_locked refresh
//   - ['battlepass']         — XP feeds the season XP gauge
//   - ['burst-leaderboard']  — leaderboard tabs re-fetch
//
// Error mapping is left to the caller. Backend `error.detail` codes are
// declared in i18n under `gamification.burst.errors.*`.

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type { BurstClaimResponse } from '@/types/gamification';

export function useClaimBurst() {
  const qc = useQueryClient();
  return useMutation<BurstClaimResponse, Error, string>({
    mutationFn: (missionId) =>
      rewardsClient.post<BurstClaimResponse>(
        `/gamification/missions/${missionId}/burst-claim`,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['missions'] });
      void qc.invalidateQueries({ queryKey: ['battlepass'] });
      void qc.invalidateQueries({ queryKey: ['burst-leaderboard'] });
    },
  });
}

export type { BurstClaimResponse } from '@/types/gamification';
