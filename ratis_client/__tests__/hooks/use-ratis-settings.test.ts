// ratis_client/__tests__/hooks/use-ratis-settings.test.ts
//
// V1.1 — runtime settings hook (F-10).

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useRatisSettings } from '@/hooks/use-ratis-settings';
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

describe('useRatisSettings', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('fetches /rewards/settings/public', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      'pipeline.jar.monthly_subscription_price_cents': 999,
    });
    const { result } = renderHook(() => useRatisSettings(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith('/rewards/settings/public');
    expect(
      result.current.data?.['pipeline.jar.monthly_subscription_price_cents'],
    ).toBe(999);
  });

  it('exposes the error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useRatisSettings(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('returns an empty dict when the backend ships no whitelisted keys', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({});
    const { result } = renderHook(() => useRatisSettings(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({});
  });
});
