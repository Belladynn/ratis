// ratis_client/hooks/use-cab-balance.ts
import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';

export interface CabBalanceResponse {
  cab_balance: number;
  battlepass: {
    season_number: number;
    season_name: string;
    ends_at: string;
    cab_earned_season: number;
    next_milestone_delta: number;
  } | null;
}

export function useCabBalance() {
  const query = useQuery<CabBalanceResponse>({
    queryKey: ['cab-balance'],
    queryFn: () => rewardsClient.get<CabBalanceResponse>('/rewards/cab/balance'),
  });

  return {
    balance: query.data?.cab_balance ?? 0,
    battlepass: query.data?.battlepass ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
