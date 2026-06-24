// ratis_client/hooks/use-missions.ts
//
// Missions query + claim mutation.
//
// Buffer + Burst (refonte 2026-05-09) refactor :
//   - `useClaimMission` now expects the multi-claim cumulatif response
//     shape `{cab_awarded, portions_claimed_total, portions_remaining,
//     mission_status}` from the backend (= same endpoint, richer payload).
//   - The mutation invalidator stays unchanged (missions / streak /
//     battlepass / savings / cab-balance) so the dashboard refreshes the
//     CAB pill, the streak ribbon and the battlepass XP gauge.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type {
  ClaimMissionResponse,
  MissionsResponse,
} from '@/types/gamification';

export function useMissions() {
  return useQuery<MissionsResponse>({
    queryKey: ['missions'],
    queryFn: () => rewardsClient.get<MissionsResponse>('/gamification/missions'),
  });
}

export function useClaimMission() {
  const qc = useQueryClient();
  return useMutation<ClaimMissionResponse, Error, string>({
    mutationFn: (missionId) =>
      rewardsClient.post<ClaimMissionResponse>(
        `/gamification/missions/${missionId}/claim`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['missions'] });
      qc.invalidateQueries({ queryKey: ['streak'] });
      qc.invalidateQueries({ queryKey: ['battlepass'] });
      qc.invalidateQueries({ queryKey: ['savings'] });
      qc.invalidateQueries({ queryKey: ['cab-balance'] });
    },
  });
}

export type { ClaimMissionResponse } from '@/types/gamification';
