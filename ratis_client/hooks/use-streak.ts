// ratis_client/hooks/use-streak.ts
import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import { StreakState } from '@/types/gamification';

export function useStreak() {
  return useQuery<StreakState>({
    queryKey: ['streak'],
    queryFn: () => rewardsClient.get<StreakState>('/gamification/streak'),
  });
}
