// ratis_client/hooks/use-gift-card-cap-usage.ts
//
// V1.1 — server-authoritative gift-card cap usage snapshot.
//
// Replaces the legacy client-side `computeUsageStats(orders)` reducer in
// `hooks/use-gift-cards.ts`. The aggregate is computed server-side from
// `users.gift_card_redeemed_ytd_cents` (denorm) + a SUM over
// `gift_card_orders` (Europe/Paris cutoff, see
// `webservices/ratis_rewards/services/gift_card_cap_usage_service.py`).
//
// Returns annual / daily / weekly windows + thresholds in a single
// payload — the shop screen reads ALL fields from this hook so the
// boutique caps display stays consistent across devices.

import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';

/** Shape of the `/rewards/gift-cards/cap-usage` JSON response. */
export interface GiftCardCapUsage {
  /** Calendar year the YTD figure refers to (server clock). */
  year: number;
  /** Cents redeemed since the start of the calendar year. */
  ytd_cents: number;
  annual_warning_threshold_cents: number;
  annual_hard_cap_cents: number;
  /** Always >= 0 — clamped if a manual UPDATE pushed ytd above the cap. */
  remaining_cents: number;
  /** True when ytd >= annual_warning_threshold_cents (BNC fiscal modal). */
  warning_threshold_reached: boolean;
  /** Cents of non-failed shop_purchase orders today (Europe/Paris). */
  daily_cents: number;
  weekly_cents: number;
  daily_cap_cents: number;
  weekly_cap_cents: number;
}

const CAP_USAGE_STALE_MS = 30_000; // 30 s — caps move fast around a purchase

export function useGiftCardCapUsage() {
  return useQuery<GiftCardCapUsage>({
    queryKey: ['gift-cards', 'cap-usage'],
    queryFn: () =>
      rewardsClient.get<GiftCardCapUsage>('/rewards/gift-cards/cap-usage'),
    staleTime: CAP_USAGE_STALE_MS,
  });
}
