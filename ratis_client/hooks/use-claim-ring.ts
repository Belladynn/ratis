// ratis_client/hooks/use-claim-ring.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/services/api-client';

export type ClaimRingAnimation = 'claimed' | 'nothing_to_claim';

export interface ClaimRingResponse {
  animation: ClaimRingAnimation;
  rings_consumed: number;
  pending_rings: number;
  subscription_price_cents: number;
}

/**
 * Atomic "break one ROI ring" mutation.
 * Each call increments rings_consumed by at most 1 on the backend. Concurrent
 * calls race cleanly — the backend returns the new rings_consumed value for
 * each winning call and `nothing_to_claim` for the rest.
 */
export function useClaimRing() {
  const qc = useQueryClient();
  return useMutation<ClaimRingResponse, Error, void>({
    mutationFn: () => apiClient.post<ClaimRingResponse>('/account/rings/claim'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['account-stats'] });
    },
  });
}
