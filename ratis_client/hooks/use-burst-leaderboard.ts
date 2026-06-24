// ratis_client/hooks/use-burst-leaderboard.ts
//
// Buffer + Burst (refonte 2026-05-09) — Burst leaderboard query.
//
// Two periods are surfaced :
//   - 'monthly'  → GET /gamification/leaderboard/burst-monthly[?month=YYYY-MM]
//   - 'alltime'  → GET /gamification/leaderboard/burst-alltime
//
// Stale time is 5 minutes — the leaderboard is read-mostly and a stale
// 30-second window in the UI is fine. Mutations to ['burst-leaderboard']
// (= `useClaimBurst`) invalidate the cache so the next read is fresh.

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type {
  BurstLeaderboardAlltimeResponse,
  BurstLeaderboardMonthlyResponse,
} from '@/types/gamification';

const STALE_TIME_MS = 5 * 60 * 1000;

export type UseBurstLeaderboardArgs =
  | { period: 'monthly'; month?: string }
  | { period: 'alltime' };

type Response =
  | BurstLeaderboardMonthlyResponse
  | BurstLeaderboardAlltimeResponse;

export function useBurstLeaderboard(
  args: UseBurstLeaderboardArgs,
): UseQueryResult<Response, Error> {
  const isMonthly = args.period === 'monthly';
  const month = isMonthly ? args.month : undefined;

  return useQuery<Response, Error>({
    // Cache key includes period + month so different views don't clobber
    // each other. The mutation invalidator uses ['burst-leaderboard']
    // (root key) → invalidates everything regardless of period/month.
    queryKey: ['burst-leaderboard', args.period, month ?? 'current'],
    queryFn: () => {
      if (args.period === 'alltime') {
        return rewardsClient.get<BurstLeaderboardAlltimeResponse>(
          '/gamification/leaderboard/burst-alltime',
        );
      }
      const qs = month ? `?month=${encodeURIComponent(month)}` : '';
      return rewardsClient.get<BurstLeaderboardMonthlyResponse>(
        `/gamification/leaderboard/burst-monthly${qs}`,
      );
    },
    staleTime: STALE_TIME_MS,
  });
}

export type {
  BurstLeaderboardEntry,
  BurstLeaderboardMonthlyResponse,
  BurstLeaderboardAlltimeResponse,
} from '@/types/gamification';
