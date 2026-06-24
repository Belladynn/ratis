// ratis_client/__tests__/hooks/use-gift-card-cap-usage.test.ts
//
// V1.1 — gift-card cap usage hook (F-11).

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useGiftCardCapUsage } from '@/hooks/use-gift-card-cap-usage';
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

const ZERO_USAGE = {
  year: 2026,
  ytd_cents: 0,
  annual_warning_threshold_cents: 30500,
  annual_hard_cap_cents: 119900,
  remaining_cents: 119900,
  warning_threshold_reached: false,
  daily_cents: 0,
  weekly_cents: 0,
  daily_cap_cents: 10000,
  weekly_cap_cents: 30000,
};

describe('useGiftCardCapUsage', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('fetches /rewards/gift-cards/cap-usage', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue(ZERO_USAGE);
    const { result } = renderHook(() => useGiftCardCapUsage(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith(
      '/rewards/gift-cards/cap-usage',
    );
    expect(result.current.data).toEqual(ZERO_USAGE);
  });

  it('exposes warning_threshold_reached at 25 % of annual cap', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      ...ZERO_USAGE,
      ytd_cents: 30500,
      remaining_cents: 89400,
      warning_threshold_reached: true,
    });
    const { result } = renderHook(() => useGiftCardCapUsage(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.warning_threshold_reached).toBe(true);
    expect(result.current.data?.remaining_cents).toBe(89400);
  });

  it('exposes the error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useGiftCardCapUsage(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('exposes 100 % cap saturation', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      ...ZERO_USAGE,
      ytd_cents: 119900,
      remaining_cents: 0,
      warning_threshold_reached: true,
    });
    const { result } = renderHook(() => useGiftCardCapUsage(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.remaining_cents).toBe(0);
  });
});
