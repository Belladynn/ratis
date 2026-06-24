// ratis_client/__tests__/hooks/use-burst-leaderboard.test.ts
//
// Buffer + Burst (refonte 2026-05-09) — useBurstLeaderboard hook tests.

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useBurstLeaderboard } from '@/hooks/use-burst-leaderboard';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn(), post: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
  return { qc, wrapper };
}

const MONTHLY_RESPONSE = {
  month: '2026-05',
  top: [
    {
      user_id: 'u1',
      display_name: 'alice',
      xp_earned: 65536,
      burst_count: 16,
      buffer_count: 0,
      mission_action_type: 'label_scan',
      mission_qualifier: null,
      recorded_at: '2026-05-08T12:00:00Z',
    },
  ],
  your_rank: 23,
  your_max_xp: 4096,
};

const ALLTIME_RESPONSE = {
  top: [
    {
      user_id: 'u1',
      display_name: 'alice',
      xp_earned: 131072,
      burst_count: 17,
      buffer_count: 0,
      mission_action_type: 'label_scan',
      mission_qualifier: null,
      recorded_at: '2026-04-21T10:00:00Z',
    },
  ],
  your_rank: 47,
  your_max_xp: 8192,
};

describe('useBurstLeaderboard', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('fetches monthly leaderboard via GET /gamification/leaderboard/burst-monthly', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MONTHLY_RESPONSE);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useBurstLeaderboard({ period: 'monthly' }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith(
      '/gamification/leaderboard/burst-monthly',
    );
    expect(result.current.data?.top).toHaveLength(1);
    if (result.current.data && 'month' in result.current.data) {
      expect(result.current.data.month).toBe('2026-05');
    }
  });

  it('passes month query string when provided', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MONTHLY_RESPONSE);
    const { wrapper } = makeWrapper();
    renderHook(
      () => useBurstLeaderboard({ period: 'monthly', month: '2026-04' }),
      { wrapper },
    );

    await waitFor(() =>
      expect(rewardsClient.get).toHaveBeenCalledWith(
        '/gamification/leaderboard/burst-monthly?month=2026-04',
      ),
    );
  });

  it('fetches all-time leaderboard via GET /gamification/leaderboard/burst-alltime', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(ALLTIME_RESPONSE);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useBurstLeaderboard({ period: 'alltime' }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith(
      '/gamification/leaderboard/burst-alltime',
    );
    expect(result.current.data?.your_rank).toBe(47);
  });

  it('returns error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useBurstLeaderboard({ period: 'monthly' }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
