// ratis_client/hooks/use-shop-usage-stats.ts
//
// V1.1 — per-brand gift-card aggregate stats for the shop screen.
//
// Replaces the client-side reducer in `app/shop/[brand_id].tsx` that
// walked the entire `useGiftCards()` payload. The aggregate is a single
// SQL `COUNT/SUM/MIN/MAX` server-side, so :
//
//   - bandwidth stays bounded as the user accrues orders
//   - the math doesn't break once the gift-cards list paginates
//   - failed orders are excluded consistently (see
//     `services/shop_usage_stats_service.py`).

import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';

/** Shape returned by `GET /rewards/shop/{brand_id}/usage-stats`. */
export interface ShopUsageStatsResponse {
  brand_id: string;
  orders_count: number;
  total_saved_cents: number;
  /** ISO 8601 — null when no order yet. */
  first_order_at: string | null;
  /** ISO 8601 — null when no order yet. */
  last_order_at: string | null;
}

const SHOP_USAGE_STALE_MS = 60_000; // 1 min — aggregate refresh after a purchase

export function useShopUsageStats(brandId: string | undefined) {
  return useQuery<ShopUsageStatsResponse>({
    // Disabled until the route param is resolved — Expo Router can hand
    // us `undefined` on first render.
    enabled: typeof brandId === 'string' && brandId.length > 0,
    queryKey: ['shop', 'usage-stats', brandId],
    queryFn: () =>
      rewardsClient.get<ShopUsageStatsResponse>(
        `/rewards/shop/${brandId}/usage-stats`,
      ),
    staleTime: SHOP_USAGE_STALE_MS,
  });
}
