// ratis_client/__tests__/hooks/use-feed-jack.test.ts
//
// Bug 7 (PO ticket 2026-05-12 wave 2) — Feed Jack mutation wired to
// `POST /api/v1/gamification/streak/feed`. Invalidates the streak, the
// cab-balance and the battlepass on success so the dashboard refreshes
// in one round-trip.

import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useFeedJack } from '@/hooks/use-feed-jack';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn(), post: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
  return { qc, wrapper };
}

const FEED_RESPONSE = {
  streak_days: 5,
  multiplier: 0.25,
  food_reserves: 0,
  already_fed_today: true,
  needs_repair: false,
  last_fed_at: '2026-05-12T08:00:00Z',
};

describe('useFeedJack', () => {
  beforeEach(() => {
    (rewardsClient.post as jest.Mock).mockReset();
  });

  it('POSTs to /gamification/streak/feed', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(FEED_RESPONSE);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useFeedJack(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(rewardsClient.post).toHaveBeenCalledWith(
      '/gamification/streak/feed',
      {},
    );
  });

  it('invalidates streak, cab-balance and battlepass on success', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(FEED_RESPONSE);
    const { qc, wrapper } = makeWrapper();
    const spy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useFeedJack(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const invalidatedKeys = spy.mock.calls.map((c) =>
      (c[0] as { queryKey: unknown[] }).queryKey?.[0],
    );
    expect(invalidatedKeys).toEqual(
      expect.arrayContaining(['streak', 'cab-balance', 'battlepass']),
    );
  });

  it('surfaces errors via the mutation state', async () => {
    (rewardsClient.post as jest.Mock).mockRejectedValue(new Error('boom'));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useFeedJack(), { wrapper });

    await act(async () => {
      try {
        await result.current.mutateAsync();
      } catch {
        /* expected */
      }
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
