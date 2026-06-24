// ratis_client/hooks/use-shop-order.ts
//
// Boutique V1 — POST a gift-card order (CAB → gift card).
//
// On success we invalidate :
//   - ['gift-cards']   — Mes cartes cadeaux re-fetches with the new pending row
//   - ['cab-balance']  — header pill / dashboard balance reflect the debit
//
// Error mapping is left to the caller (catalog of error.detail strings is
// declared in i18n under `shop.errors.*`).

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type { ShopOrderInput, ShopOrderResponse } from '@/types/shop';

export function useShopOrder() {
  const qc = useQueryClient();
  return useMutation<ShopOrderResponse, Error, ShopOrderInput>({
    mutationFn: (input) =>
      rewardsClient.post<ShopOrderResponse>('/rewards/gift-cards/order', input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['gift-cards'] });
      void qc.invalidateQueries({ queryKey: ['cab-balance'] });
    },
  });
}

export type { ShopOrderInput, ShopOrderResponse } from '@/types/shop';
