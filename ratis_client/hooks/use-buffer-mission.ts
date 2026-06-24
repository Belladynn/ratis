// ratis_client/hooks/use-buffer-mission.ts
//
// Buffer + Burst (refonte 2026-05-09) — apply 1 Buffer to a daily mission.
//
// Effects (server-side, atomic) :
//   - buffer_count          += 1
//   - target_count          *= 2
//   - cab_reward             = R_original × (buffer_count + 1)
//   - period_extended_until  = period_start + (buffer_count + 1) days
//
// On success we invalidate :
//   - ['missions']     — UI re-renders with new buffer_count + target_count
//   - ['cab-balance']  — header pill stays in sync (no debit but symmetry)
//
// Error mapping is left to the caller. Backend `error.detail` codes are
// declared in i18n under `gamification.buffer.errors.*`.

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type { BufferMissionResponse } from '@/types/gamification';

export function useBufferMission() {
  const qc = useQueryClient();
  return useMutation<BufferMissionResponse, Error, string>({
    mutationFn: (missionId) =>
      rewardsClient.post<BufferMissionResponse>(
        `/gamification/missions/${missionId}/buffer`,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['missions'] });
      void qc.invalidateQueries({ queryKey: ['cab-balance'] });
    },
  });
}

export type { BufferMissionResponse } from '@/types/gamification';
