import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useStreak } from '@/hooks/use-streak';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useStreak', () => {
  it('returns streak data on success', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      streak_days: 7, multiplier: 0.35, food_reserves: 2,
      already_fed_today: true, needs_repair: false, last_fed_at: '2026-04-20',
    });

    const { result } = renderHook(() => useStreak(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.streak_days).toBe(7);
  });

  it('returns error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('network'));

    const { result } = renderHook(() => useStreak(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
