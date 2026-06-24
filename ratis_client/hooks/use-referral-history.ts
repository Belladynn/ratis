// ratis_client/hooks/use-referral-history.ts
// GET /rewards/referral/history — list of filleuls + aggregated stats. The
// backend returns only display_name (never email / user_id) for RGPD safety,
// and nulls it out for soft-deleted users.

import { useQuery } from "@tanstack/react-query";
import { rewardsClient } from "@/services/rewards-client";

export type ReferralUseStatus = "pending" | "rewarded";
export type ReferralPlan = "monthly" | "annual" | null;

export interface ReferralUse {
  /** Display name of the referred user — null if deleted or never set. */
  referred_user_display_name: string | null;
  plan: ReferralPlan;
  status: ReferralUseStatus;
  rewarded_at: string | null;
  created_at: string;
}

export interface ReferralStats {
  total_uses: number;
  rewarded_uses: number;
  total_cab_earned: number;
}

export interface ReferralHistory {
  code: string;
  stats: ReferralStats;
  uses: ReferralUse[];
}

export function useReferralHistory() {
  return useQuery<ReferralHistory>({
    queryKey: ["referral-history"],
    queryFn: () =>
      rewardsClient.get<ReferralHistory>("/rewards/referral/history"),
    // History updates when a filleul signs up / subscribes — 2 minute stale
    // time balances freshness (user wants to see new referrals fast) vs
    // cost (no need to re-fetch on every screen mount).
    staleTime: 2 * 60_000,
  });
}
