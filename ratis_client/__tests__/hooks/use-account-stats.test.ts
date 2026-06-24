import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useAccountStats } from '@/hooks/use-account-stats';
import { apiClient } from '@/services/api-client';

jest.mock('@/services/api-client', () => {
  const actual = jest.requireActual('@/services/api-client');
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  };
});

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const STATS = {
  total_scans: 142,
  unique_products: 98,
  total_savings_cents: 3400,
  today_savings_cents: 250,
  location_missing: false,
  member_since: '2025-11-01T10:00:00+00:00',
  rings: {
    rings_consumed: 4,
    pending_rings: 0,
    subscription_price_cents: 799,
  },
};

describe('useAccountStats', () => {
  beforeEach(() => {
    (apiClient.get as jest.Mock).mockReset();
  });

  it('fetches stats from /account/stats', async () => {
    (apiClient.get as jest.Mock).mockResolvedValue(STATS);
    const { result } = renderHook(() => useAccountStats(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiClient.get).toHaveBeenCalledWith('/account/stats');
    expect(result.current.data).toEqual(STATS);
  });

  it('exposes error state on failure', async () => {
    (apiClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useAccountStats(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
