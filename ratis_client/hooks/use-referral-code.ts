// ratis_client/hooks/use-referral-code.ts
// GET /rewards/referral/code — returns the user's own referral code. The
// backend lazy-creates it on first access so this hook never 404s.

import { useQuery } from "@tanstack/react-query";
import { rewardsClient } from "@/services/rewards-client";

export interface ReferralCode {
  code: string;
  created_at: string;
}

export function useReferralCode() {
  return useQuery<ReferralCode>({
    queryKey: ["referral-code"],
    queryFn: () => rewardsClient.get<ReferralCode>("/rewards/referral/code"),
    // The code rarely changes — cache aggressively. The user can pull-to-refresh
    // the Referral screen if they somehow need a fresh fetch.
    staleTime: 60 * 60_000, // 1 hour
  });
}
