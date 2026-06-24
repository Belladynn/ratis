// ratis_client/__tests__/hooks/use-achievements.test.ts
//
// Achievements V1 — hook contract tests (PR 8/8).
import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import {
  useAchievements,
  useAchievementDetail,
} from '@/hooks/use-achievements';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn(), post: jest.fn() },
  triggerSecretEvent: jest.fn(),
}));

const MOCK_LIST = {
  categories: [
    {
      key: 'volume',
      label: 'Scans',
      items: [
        {
          id: 'aaaa-1111',
          code: 'v_first',
          label: 'Premier scan',
          description: 'Scanner ton tout premier ticket',
          icon: '🎬',
          rarity: 'terracotta',
          category: 'volume',
          cab_reward: 20,
          target_value: 1,
          progress: null,
          unlocked: true,
          unlocked_at: '2026-04-15T08:30:00+00:00',
          window_open: true,
        },
      ],
    },
  ],
};

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useAchievements', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('returns the list shape on success', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MOCK_LIST);
    const { result } = renderHook(() => useAchievements(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.categories[0].items[0].code).toBe('v_first');
  });

  it('hits GET /rewards/achievements without query string by default', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MOCK_LIST);
    renderHook(() => useAchievements(), { wrapper: makeWrapper() });
    await waitFor(() =>
      expect(rewardsClient.get).toHaveBeenCalledWith('/rewards/achievements'),
    );
  });

  it('serialises optional filters as query string', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MOCK_LIST);
    renderHook(
      () => useAchievements({ category: 'volume', unlocked: 'true' }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() =>
      expect(rewardsClient.get).toHaveBeenCalledWith(
        '/rewards/achievements?category=volume&unlocked=true',
      ),
    );
  });

  it('uses a stable query key per filter combo', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(MOCK_LIST);
    const wrapper = makeWrapper();
    const { rerender } = renderHook(
      ({ cat }: { cat?: string }) =>
        useAchievements(cat ? { category: cat } : undefined),
      { wrapper, initialProps: { cat: undefined } },
    );
    await waitFor(() => expect(rewardsClient.get).toHaveBeenCalledTimes(1));
    rerender({ cat: 'volume' });
    await waitFor(() => expect(rewardsClient.get).toHaveBeenCalledTimes(2));
    // Same filter again → cache hit (no extra fetch).
    rerender({ cat: 'volume' });
    expect(rewardsClient.get).toHaveBeenCalledTimes(2);
  });

  it('exposes error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useAchievements(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useAchievementDetail', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('is disabled when achievementId is null', async () => {
    const { result } = renderHook(() => useAchievementDetail(null), {
      wrapper: makeWrapper(),
    });
    // disabled query → never fires the queryFn.
    expect(rewardsClient.get).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(false);
  });

  it('fetches GET /rewards/achievements/{id} when id present', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(
      MOCK_LIST.categories[0].items[0],
    );
    const { result } = renderHook(
      () => useAchievementDetail('aaaa-1111'),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith(
      '/rewards/achievements/aaaa-1111',
    );
    expect(result.current.data?.code).toBe('v_first');
  });
});
