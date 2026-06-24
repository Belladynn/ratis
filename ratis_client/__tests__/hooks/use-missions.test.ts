// ratis_client/__tests__/hooks/use-missions.test.ts
import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useMissions, useClaimMission } from '@/hooks/use-missions';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn(), post: jest.fn() },
}));

const MOCK_RESPONSE = {
  daily: {
    date: '2026-04-20',
    missions: [
      { id: '1', action_type: 'receipt_scan', difficulty: 'easy', target_count: 1, current_count: 0, cab_reward: 50, xp_reward: 10, status: 'active' },
    ],
  },
  weekly: {
    week_start: '2026-04-14',
    missions: [
      { id: 'w1', action_type: 'label_scan', difficulty: 'medium', target_count: 3, current_count: 1, cab_reward: 75, xp_reward: 30, status: 'active' },
    ],
  },
};

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
  return { qc, wrapper };
}

describe('useMissions', () => {
  it('returns daily and weekly missions on success', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MOCK_RESPONSE);

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useMissions(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.daily.missions).toHaveLength(1);
    expect(result.current.data?.weekly.missions).toHaveLength(1);
    expect(result.current.data?.daily.missions[0].action_type).toBe('receipt_scan');
    expect(result.current.data?.daily.missions[0].xp_reward).toBe(10);
  });

  it('calls GET /gamification/missions', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MOCK_RESPONSE);
    const { wrapper } = makeWrapper();
    renderHook(() => useMissions(), { wrapper });
    await waitFor(() => expect(rewardsClient.get).toHaveBeenCalledWith('/gamification/missions'));
  });

  it('returns error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useMissions(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useClaimMission', () => {
  it('calls POST /gamification/missions/{id}/claim', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue({
      cab_awarded: 50,
      portions_claimed_total: 1,
      portions_remaining: 0,
      mission_status: 'claimed',
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useClaimMission(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync('mission-1');
    });

    expect(rewardsClient.post).toHaveBeenCalledWith('/gamification/missions/mission-1/claim');
  });

  it('returns the multi-claim response shape (cab_awarded + portions)', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue({
      cab_awarded: 100,
      portions_claimed_total: 2,
      portions_remaining: 1,
      mission_status: 'pending',
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useClaimMission(), { wrapper });

    let data;
    await act(async () => {
      data = await result.current.mutateAsync('mission-1');
    });

    expect(data).toEqual({
      cab_awarded: 100,
      portions_claimed_total: 2,
      portions_remaining: 1,
      mission_status: 'pending',
    });
  });

  it('invalidates missions, streak, battlepass, savings, cab-balance on success', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue({
      cab_awarded: 0,
      portions_claimed_total: 1,
      portions_remaining: 0,
      mission_status: 'claimed',
    });
    const { qc, wrapper } = makeWrapper();
    const spy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useClaimMission(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync('mission-1');
    });

    const keys = spy.mock.calls.map(c => (c[0] as { queryKey: string[] }).queryKey[0]);
    expect(keys).toEqual(expect.arrayContaining(['missions', 'streak', 'battlepass', 'savings', 'cab-balance']));
  });

  it('exposes error.detail on backend conflict (e.g. mission_expired)', async () => {
    (rewardsClient.post as jest.Mock).mockRejectedValue(
      Object.assign(new Error('mission_expired'), {
        detail: 'mission_expired',
        status: 410,
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useClaimMission(), { wrapper });

    await expect(
      act(async () => {
        await result.current.mutateAsync('mission-1');
      }),
    ).rejects.toThrow('mission_expired');

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
