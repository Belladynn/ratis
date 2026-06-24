// ratis_client/__tests__/hooks/use-shop-usage-stats.test.ts
//
// V1.1 — per-brand shop usage stats hook (F-13).

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useShopUsageStats } from '@/hooks/use-shop-usage-stats';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const BRAND_ID = '11111111-1111-1111-1111-111111111111';

const NON_EMPTY = {
  brand_id: BRAND_ID,
  orders_count: 12,
  total_saved_cents: 4350,
  first_order_at: '2025-08-12T10:23:00Z',
  last_order_at: '2026-04-30T18:42:00Z',
};

describe('useShopUsageStats', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('fetches /rewards/shop/{brand_id}/usage-stats', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(NON_EMPTY);
    const { result } = renderHook(() => useShopUsageStats(BRAND_ID), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith(
      `/rewards/shop/${BRAND_ID}/usage-stats`,
    );
    expect(result.current.data).toEqual(NON_EMPTY);
  });

  it('skips the fetch when brandId is undefined', async () => {
    const { result } = renderHook(
      () => useShopUsageStats(undefined as unknown as string),
      { wrapper: makeWrapper() },
    );
    // disabled queries never fire and stay in idle state.
    expect(result.current.fetchStatus).toBe('idle');
    expect(rewardsClient.get).not.toHaveBeenCalled();
  });

  it('handles the empty-stats shape (brand never used)', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      brand_id: BRAND_ID,
      orders_count: 0,
      total_saved_cents: 0,
      first_order_at: null,
      last_order_at: null,
    });
    const { result } = renderHook(() => useShopUsageStats(BRAND_ID), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.orders_count).toBe(0);
    expect(result.current.data?.first_order_at).toBeNull();
  });

  it('exposes the error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useShopUsageStats(BRAND_ID), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
