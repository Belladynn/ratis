// ratis_client/hooks/use-account-stats.ts
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/services/api-client';

export interface RingsState {
  /** Number of ROI rings the user has already broken (lifetime, never decreases). */
  rings_consumed: number;
  /** Rings ready to be broken now (claim available). */
  pending_rings: number;
  /** Monthly subscription price in cents — used to derive rings. */
  subscription_price_cents: number;
}

export interface AccountStats {
  total_scans: number;
  unique_products: number;
  /** Lifetime savings in cents — snapshot + live delta. */
  total_savings_cents: number;
  /** Savings accumulated since UTC midnight today. */
  today_savings_cents: number;
  /** True when the user has no ref_lat/ref_lng — frontend should prompt for location. */
  location_missing: boolean;
  member_since: string | null;
  rings: RingsState;
}

/**
 * Aggregated user stats for the Profil screen + dashboard ROI rings.
 * GET /account/stats (ratis_auth).
 */
export function useAccountStats() {
  return useQuery<AccountStats>({
    queryKey: ['account-stats'],
    queryFn: () => apiClient.get<AccountStats>('/account/stats'),
  });
}
