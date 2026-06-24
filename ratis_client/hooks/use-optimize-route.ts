// ratis_client/hooks/use-optimize-route.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { listClient } from '@/services/list-client';

export interface OptimizeVars {
  lat: number;
  lng: number;
}

export interface OptimizeResponse {
  id: string;
  list_id: string;
  status: 'computing';
}

/**
 * Wraps POST /lists/{list_id}/optimize.
 * Fire-and-forget — returns 202 + slim {id, status: "computing"}.
 * On success, invalidates ['route', listId] so the polling query refetches
 * and starts its 2 s refetchInterval until the worker flips the status to ready.
 *
 * If `listId` is null, calling .mutate() rejects synchronously with
 * `Error('no_active_list')`.
 */
export function useOptimizeRoute(listId: string | null) {
  const qc = useQueryClient();
  return useMutation<OptimizeResponse, Error, OptimizeVars>({
    mutationFn: async ({ lat, lng }) => {
      if (!listId) throw new Error('no_active_list');
      return listClient.post<OptimizeResponse>(
        `/lists/${listId}/optimize`,
        { lat, lng },
      );
    },
    onSuccess: () => {
      if (listId) qc.invalidateQueries({ queryKey: ['route', listId] });
    },
  });
}
