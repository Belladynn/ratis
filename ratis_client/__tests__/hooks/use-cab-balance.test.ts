// ratis_client/__tests__/hooks/use-cab-balance.test.ts
import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useCabBalance } from '@/hooks/use-cab-balance';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useCabBalance', () => {
  it('returns balance and battlepass on success', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      cab_balance: 1240,
      battlepass: {
        season_number: 1,
        season_name: 'Saison 1',
        ends_at: '2026-06-30T00:00:00',
        cab_earned_season: 500,
        next_milestone_delta: 200,
      },
    });

    const { result } = renderHook(() => useCabBalance(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.balance).toBe(1240);
    expect(result.current.battlepass?.season_number).toBe(1);
    expect(result.current.battlepass?.next_milestone_delta).toBe(200);
    expect(result.current.isError).toBe(false);
  });

  it('returns 0 balance when no battlepass season active', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      cab_balance: 320,
      battlepass: null,
    });

    const { result } = renderHook(() => useCabBalance(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.balance).toBe(320);
    expect(result.current.battlepass).toBeNull();
  });

  it('returns error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('network'));

    const { result } = renderHook(() => useCabBalance(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.balance).toBe(0);
    expect(result.current.battlepass).toBeNull();
  });
});
