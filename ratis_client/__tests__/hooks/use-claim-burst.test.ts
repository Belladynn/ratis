// ratis_client/__tests__/hooks/use-claim-burst.test.ts
//
// Buffer + Burst (refonte 2026-05-09) — useClaimBurst hook tests.

import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useClaimBurst } from '@/hooks/use-claim-burst';
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

const BURST_RESPONSE = {
  xp_awarded: 320,
  burst_count_total: 3,
  burst_locked: true,
  leaderboard_record_updated: true,
};

describe('useClaimBurst', () => {
  beforeEach(() => {
    (rewardsClient.post as jest.Mock).mockReset();
  });

  it('POSTs to /gamification/missions/{id}/burst-claim', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(BURST_RESPONSE);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useClaimBurst(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync('mission-1');
    });

    expect(rewardsClient.post).toHaveBeenCalledWith(
      '/gamification/missions/mission-1/burst-claim',
    );
  });

  it('invalidates missions, battlepass and burst-leaderboard on success', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(BURST_RESPONSE);
    const { qc, wrapper } = makeWrapper();
    const spy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useClaimBurst(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync('mission-1');
    });

    const keys = spy.mock.calls.map(
      (c) => (c[0] as { queryKey: string[] }).queryKey[0],
    );
    expect(keys).toEqual(
      expect.arrayContaining(['missions', 'battlepass', 'burst-leaderboard']),
    );
  });

  it('exposes error.detail when no burst palier unlocked yet', async () => {
    (rewardsClient.post as jest.Mock).mockRejectedValue(
      Object.assign(new Error('no_burst_palier_unlocked'), {
        detail: 'no_burst_palier_unlocked',
        status: 402,
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useClaimBurst(), { wrapper });

    await expect(
      act(async () => {
        await result.current.mutateAsync('mission-1');
      }),
    ).rejects.toThrow('no_burst_palier_unlocked');

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
