// ratis_client/hooks/use-scan-confirm-store.ts
//
// Mutation wrapping `POST /api/v1/scan/receipt/{receipt_id}/confirm-store` —
// the user confirms the OCR-detected store header for a receipt whose store is
// `unknown` or `pending`. The endpoint takes no body: the backend reads the
// `store_candidates` table for the receipt and creates a `user_suggested`
// store with `validation_status='pending'` (cashback frozen until consensus
// validates the store via the daily batch — see ARCH_store_validation.md).
//
// On success the unified history list and the receipt detail caches are
// invalidated so the badge / pen colour update without a manual refetch.

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';
import { receiptItemsQueryKey } from '@/hooks/use-receipt-items';

export interface ConfirmStoreResponse {
  store_status: 'pending';
  store_id: string;
  validation_status: 'pending';
  message: string;
}

export function useScanConfirmStore(receiptId: string) {
  const qc = useQueryClient();
  return useMutation<ConfirmStoreResponse, Error, void>({
    mutationFn: () =>
      productClient.post<ConfirmStoreResponse>(
        `/scan/receipt/${receiptId}/confirm-store`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scan-history'] });
      qc.invalidateQueries({ queryKey: receiptItemsQueryKey(receiptId) });
      qc.invalidateQueries({ queryKey: ['receipt', receiptId] });
    },
  });
}
