// ratis_client/__tests__/hooks/use-shop-order.test.ts
//
// Boutique V1 phase 2 (frontend) — order mutation hook.
//
// On success we invalidate ['gift-cards'] (the user's gift-card list) and
// ['cab-balance'] (the spent CAB has updated the balance) so the dashboard +
// "Mes cartes cadeaux" surfaces refresh.

import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useShopOrder } from '@/hooks/use-shop-order';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn(), post: jest.fn() },
}));

function makeWrapper() {
  return (qc: QueryClient) =>
    ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);
}

const BRAND_ID = '11111111-1111-1111-1111-111111111111';

const ORDER_RESPONSE = {
  order_id: '99999999-9999-9999-9999-999999999999',
  brand: 'Amazon.fr',
  denomination_cents: 2000,
  cab_cost: 100000,
  new_cab_balance: 32500,
  status: 'pending',
  estimated_arrival: 'in a few seconds',
};

describe('useShopOrder', () => {
  beforeEach(() => {
    (rewardsClient.post as jest.Mock).mockReset();
  });

  it('POSTs to /rewards/gift-cards/order with the brand and denomination', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(ORDER_RESPONSE);
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result } = renderHook(() => useShopOrder(), {
      wrapper: makeWrapper()(qc),
    });

    await act(async () => {
      await result.current.mutateAsync({
        brand_id: BRAND_ID,
        denomination_cents: 2000,
      });
    });

    expect(rewardsClient.post).toHaveBeenCalledWith(
      '/rewards/gift-cards/order',
      { brand_id: BRAND_ID, denomination_cents: 2000 },
    );
  });

  it('invalidates gift-cards and cab-balance queries on success', async () => {
    (rewardsClient.post as jest.Mock).mockResolvedValue(ORDER_RESPONSE);
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const spy = jest.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useShopOrder(), {
      wrapper: makeWrapper()(qc),
    });

    await act(async () => {
      await result.current.mutateAsync({
        brand_id: BRAND_ID,
        denomination_cents: 2000,
      });
    });

    expect(spy).toHaveBeenCalledWith({ queryKey: ['gift-cards'] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ['cab-balance'] });
  });

  it('exposes error.detail when the API rejects with insufficient_cab_balance', async () => {
    (rewardsClient.post as jest.Mock).mockRejectedValue(
      Object.assign(new Error('insufficient_cab_balance'), {
        detail: 'insufficient_cab_balance',
        status: 402,
      }),
    );
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result } = renderHook(() => useShopOrder(), {
      wrapper: makeWrapper()(qc),
    });

    await expect(
      act(async () => {
        await result.current.mutateAsync({
          brand_id: BRAND_ID,
          denomination_cents: 5000,
        });
      }),
    ).rejects.toThrow('insufficient_cab_balance');

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
