// ratis_client/hooks/use-scan-check.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { listClient } from '@/services/list-client';

export type ScanCheckStatus = 'checked' | 'already_checked' | 'not_in_list';

export interface ScanCheckItem {
  id: string;
  product_ean: string;
  name: string;
  quantity: number;
  checked: boolean;
}

export interface ScanCheckResponse {
  status: ScanCheckStatus;
  item?: ScanCheckItem;
  product?: { ean: string; name: string } | null;
}

export interface ScanCheckVariables {
  productEan: string;
}

/**
 * Wraps POST /lists/{list_id}/scan-check.
 * Returns a useMutation that invalidates cached list data on success.
 *
 * If `listId` is null, calling .mutate() will reject synchronously (safeguard).
 */
export function useScanCheck(listId: string | null) {
  const qc = useQueryClient();
  return useMutation<ScanCheckResponse, Error, ScanCheckVariables>({
    mutationFn: async ({ productEan }) => {
      if (!listId) {
        throw new Error('no_active_list');
      }
      return listClient.post<ScanCheckResponse>(
        `/lists/${listId}/scan-check`,
        { product_ean: productEan },
      );
    },
    onSuccess: () => {
      if (listId) {
        qc.invalidateQueries({ queryKey: ['lists', listId] });
        qc.invalidateQueries({ queryKey: ['list-items', listId] });
      }
    },
  });
}
