// ratis_client/__tests__/hooks/use-buffer-mission.test.ts
//
// Buffer + Burst (refonte 2026-05-09) — useBufferMission hook tests.

import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useBufferMission } from '@/hooks/use-buffer-mission';
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

const BUFFER_RESPONSE = {
  buffer_count: 1,
  target_count: 6,
  cab_reward: 200,
  period_extended_until: '2026-05-12T00:00:00Z',
};

describe('useBufferMission', () => {
  beforeEach(() => {
    (rewardsClient.post as jest.Mock).mockReset();
  });

  it('POSTs to /gamification/missions/{id}/buffer', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(BUFFER_RESPONSE);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useBufferMission(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync('mission-1');
    });

    expect(rewardsClient.post).toHaveBeenCalledWith(
      '/gamification/missions/mission-1/buffer',
    );
  });

  it('invalidates missions and cab-balance on success', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(BUFFER_RESPONSE);
    const { qc, wrapper } = makeWrapper();
    const spy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useBufferMission(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync('mission-1');
    });

    const keys = spy.mock.calls.map(
      (c) => (c[0] as { queryKey: string[] }).queryKey[0],
    );
    expect(keys).toEqual(expect.arrayContaining(['missions', 'cab-balance']));
  });

  it('exposes error.detail on backend conflict (e.g. buffer_cap_reached)', async () => {
    (rewardsClient.post as jest.Mock).mockRejectedValue(
      Object.assign(new Error('buffer_cap_reached'), {
        detail: 'buffer_cap_reached',
        status: 409,
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useBufferMission(), { wrapper });

    await expect(
      act(async () => {
        await result.current.mutateAsync('mission-1');
      }),
    ).rejects.toThrow('buffer_cap_reached');

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
