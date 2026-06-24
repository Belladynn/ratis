// ratis_client/__tests__/hooks/use-shop-catalog.test.ts
//
// Boutique V1 phase 2 (frontend) — catalog hook.
//
// The catalog endpoint returns the list of currently *active* gift card brands
// for the running season (rotation-aware). 5min stale time fits the rotation
// cadence (saisonnière) — no need to refetch every minute.

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useShopCatalog } from '@/hooks/use-shop-catalog';
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

const BRAND_AMAZON = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'Amazon.fr',
  logo_url: 'https://example.com/amazon.png',
  is_active: true,
};

const BRAND_CARREFOUR = {
  id: '22222222-2222-2222-2222-222222222222',
  name: 'Carrefour',
  logo_url: null,
  is_active: true,
};

describe('useShopCatalog', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('fetches the active brand catalog from /rewards/gift-cards/catalog', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      brands: [BRAND_AMAZON, BRAND_CARREFOUR],
    });
    const { result } = renderHook(() => useShopCatalog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith(
      '/rewards/gift-cards/catalog',
    );
    expect(result.current.data?.brands).toEqual([
      BRAND_AMAZON,
      BRAND_CARREFOUR,
    ]);
  });

  it('returns an empty brands list when the season has no active brands', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({ brands: [] });
    const { result } = renderHook(() => useShopCatalog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.brands).toEqual([]);
  });

  it('exposes the error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useShopCatalog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
