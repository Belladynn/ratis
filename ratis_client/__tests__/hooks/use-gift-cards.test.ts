// ratis_client/__tests__/hooks/use-gift-cards.test.ts
//
// Boutique V1 phase 2 (frontend) — gift cards list + client-side usage stats.

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import {
  useGiftCards,
  computeUsageStats,
  type GiftCardOrder,
} from '@/hooks/use-gift-cards';
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

const ORDER_BASE = {
  id: 'o1',
  brand: {
    id: 'b1',
    name: 'Amazon.fr',
    logo_url: null,
  },
  code: null,
  issued_at: null,
  failed_at: null,
};

describe('useGiftCards', () => {
  beforeEach(() => {
    (rewardsClient.get as jest.Mock).mockReset();
  });

  it('fetches gift cards from /rewards/gift-cards', async () => {
    const ORDERS: GiftCardOrder[] = [
      {
        ...ORDER_BASE,
        denomination: 2000,
        status: 'pending',
        source_type: 'shop_purchase',
        created_at: new Date().toISOString(),
      },
    ];
    (rewardsClient.get as jest.Mock).mockResolvedValue(ORDERS);
    const { result } = renderHook(() => useGiftCards(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith('/rewards/gift-cards');
    expect(result.current.data).toEqual(ORDERS);
  });
});

describe('computeUsageStats', () => {
  it('returns 0/0 for an empty list', () => {
    expect(computeUsageStats([])).toEqual({
      daily_cents: 0,
      weekly_cents: 0,
    });
  });

  it('returns 0/0 for undefined input', () => {
    expect(computeUsageStats(undefined)).toEqual({
      daily_cents: 0,
      weekly_cents: 0,
    });
  });

  it('sums shop_purchase orders made today (Europe/Paris)', () => {
    const now = new Date('2026-05-08T15:00:00+02:00'); // Paris afternoon
    const orders: GiftCardOrder[] = [
      {
        ...ORDER_BASE,
        id: 'a',
        denomination: 2000,
        status: 'pending',
        source_type: 'shop_purchase',
        created_at: '2026-05-08T10:00:00+02:00',
      },
      {
        ...ORDER_BASE,
        id: 'b',
        denomination: 1000,
        status: 'issued',
        source_type: 'shop_purchase',
        created_at: '2026-05-08T13:00:00+02:00',
      },
    ];
    expect(computeUsageStats(orders, now)).toEqual({
      daily_cents: 3000,
      weekly_cents: 3000,
    });
  });

  it('does not count yesterday in daily but counts in weekly', () => {
    // 2026-05-08 is a Friday in Paris; same ISO week as Thursday.
    const now = new Date('2026-05-08T15:00:00+02:00');
    const orders: GiftCardOrder[] = [
      {
        ...ORDER_BASE,
        id: 'a',
        denomination: 2000,
        status: 'pending',
        source_type: 'shop_purchase',
        created_at: '2026-05-07T20:00:00+02:00', // yesterday Paris
      },
    ];
    expect(computeUsageStats(orders, now)).toEqual({
      daily_cents: 0,
      weekly_cents: 2000,
    });
  });

  it('skips failed orders and non-shop sources', () => {
    const now = new Date('2026-05-08T15:00:00+02:00');
    const orders: GiftCardOrder[] = [
      {
        ...ORDER_BASE,
        id: 'a',
        denomination: 5000,
        status: 'failed',
        source_type: 'shop_purchase',
        created_at: '2026-05-08T10:00:00+02:00',
      },
      {
        ...ORDER_BASE,
        id: 'b',
        denomination: 5000,
        status: 'issued',
        source_type: 'annual_subscription',
        created_at: '2026-05-08T10:00:00+02:00',
      },
    ];
    expect(computeUsageStats(orders, now)).toEqual({
      daily_cents: 0,
      weekly_cents: 0,
    });
  });
});
